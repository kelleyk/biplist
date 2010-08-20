from collections import namedtuple
from cStringIO import StringIO
import datetime
import plistlib
from struct import pack, unpack

class InvalidPlistException(Exception):
    pass

class NotBinaryPlistException(Exception):
    pass

def readPlist(pathOrFile):
    """Raises NotBinaryPlistException, InvalidPlistException"""
    didOpen = False
    result = None
    if isinstance(pathOrFile, (str, unicode)):
        pathOrFile = open(pathOrFile)
        didOpen = True
    try:
        reader = PlistReader(pathOrFile)
        result = reader.parse()
    except NotBinaryPlistException, e:
        try:
            result = plistlib.readPlist(pathOrFile)
        except Exception, e:
            raise InvalidPlistException(e)
    if didOpen:
        pathOrFile.close()
    return result

def writePlist(rootObject, pathOrFile, binary=True):
    if not binary:
        return plistlib.writePlist(rootObject, pathOrFile)
    else:
        didOpen = False
        if isinstance(pathOrFile, (str, unicode)):
            pathOrFile = open(pathOrFile, 'w')
            didOpen = True
        writer = PlistWriter(pathOrFile, rootObject)
        result = writer.writeRoot()
        if didOpen:
            pathOrFile.close()
        return result

def readPlistFromString(data):
    return readPlist(StringIO(data))

def writePlistToString(rootObject, binary=True):
    if not binary:
        return plistlib.writePlistToString(rootObject)
    else:
        io = StringIO()
        writeRoot(rootObject, io)
        return io.getvalue()

def is_stream_binary_plist(stream):
    stream.seek(0)
    header = stream.read(7)
    if header == 'bplist0':
        return True
    else:
        return False

class PlistWriter(object):
    def __init__(self, fileOrStream, rootObject):
        self.file = fileOrStream
        self.writeRoot(rootObject)
    def writeRoot(self, rootObject):
        self.uniqueObjects = []
        self.objectCount = 0
        pass

PlistTrailer = namedtuple('PlistTrailer', 'offsetSize, objectRefSize, offsetCount, topLevelObjectNumber, offsetTableOffset')

class Uid(int):
    pass

class PlistReader(object):
    file = None
    contents = ''
    offsets = None
    root = None
    trailer = None
    uniques = None
    currentOffset = 0
    
    def __init__(self, fileOrStream):
        """Raises NotBinaryPlistException."""
        self.reset()
        self.file = fileOrStream
    
    def parse(self):
        return self.readRoot()
    
    def reset(self):
        self.trailer = None
        self.contents = ''
        self.offsets = []
        self.uniques = []
        self.root = None
        self.currentOffset = 0
    
    def readRoot(self):
        self.reset()
        # Get the header, make sure it's a valid file.
        if not is_stream_binary_plist(self.file):
            raise NotBinaryPlistException()
        self.file.seek(0)
        self.contents = self.file.read()
        if len(self.contents) < 32:
            raise InvalidPlistException("File is too short.")
        trailerContents = self.contents[-32:]
        try:
            self.trailer = PlistTrailer._make(unpack("!xxxxxxBBQQQ", trailerContents))
            print "trailer:", self.trailer
            offset_size = self.trailer.offsetSize * self.trailer.offsetCount
            offset = self.trailer.offsetTableOffset
            offset_contents = self.contents[offset:offset+offset_size]
            if len(offset_contents) != self.trailer.offsetCount:
                raise InvalidPlistException("Invalid offset count. Expcted %d got %d." % (self.trailer.offsetCount, len(offset_contents)))
            offset_i = 0
            while offset_i < self.trailer.offsetCount:
                tmp_contents = offset_contents[self.trailer.offsetSize*offset_i:]
                tmp_sized = self.getSizedInteger(tmp_contents, self.trailer.offsetSize)
                self.offsets.append(tmp_sized)
                offset_i += 1
            self.setCurrentOffsetToObjectNumber(self.trailer.topLevelObjectNumber)
            self.root = self.readObject()
        except TypeError, e:
            raise InvalidPlistException(e)
        print "root is:", self.root
        raise 1
        return self.root
    
    def setCurrentOffsetToObjectNumber(self, objectNumber):
        self.currentOffset = self.offsets[objectNumber]
    
    def readObject(self):
        result = None
        tmp_byte = self.contents[self.currentOffset:self.currentOffset+1]
        marker_byte = unpack("!B", tmp_byte)[0]
        format = (marker_byte >> 4) & 0x0f
        extra = marker_byte & 0x0f
        self.currentOffset += 1
        # bool or fill byte
        if format == 0b0000:
            if extra == 0b1000:
                result = False
            elif extra == 0b1001:
                result = True
            elif extra == 0b1111:
                pass # fill byte
            else:
                raise InvalidPlistException("Invalid object found.")
        # int
        elif format == 0b0001:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readInteger(pow(2, extra))
        # real
        elif format == 0b0010:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readReal(extra)
        # date
        elif format == 0b0011 and extra == 0b0011:
            result = self.readDate()
        # data
        elif format == 0b0100:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readData(extra)
        # ascii string
        elif format == 0b0101:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readAsciiString(extra)
        # Unicode string
        elif format == 0b0110:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readUnicode(extra)
        # uid
        elif format == 0b1000:
            result = self.readUid(extra)
        # array
        elif format == 0b1010:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readArray(extra)
        # set
        elif format == 0b1100:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = set(self.readArray(extra))
        # dict
        elif format == 0b1101:
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            result = self.readDict(extra)
        else:    
            raise InvalidPlistException("Invalid object found: {format: %s, extra: %s}" % (bin(format), bin(extra)))
        return result
    
    def readInteger(self, bytes):
        result = 0
        original_offset = self.currentOffset
        data = self.contents[self.currentOffset:self.currentOffset+bytes]
        # 1, 2, and 4 byte integers are unsigned
        if bytes == 1:
            result = unpack('>B', data)[0]
        elif bytes == 2:
            result = unpack('>H', data)[0]
        elif bytes == 4:
            result = unpack('>L', data)[0]
        elif bytes == 8:
            result = unpack('>q', data)[0]
        else:
            #!! This doesn't work?
            i = 0
            while i < bytes:
                self.currentOffset += 1
                result += (result << 8) + unpack('>B', self.contents[i])[0]
                i += 1
        self.currentOffset = original_offset + bytes
        return result
    
    def readReal(self, length):
        result = 0.0
        to_read = pow(2, length)
        data = self.contents[self.currentOffset:self.currentOffset+to_read]
        if length == 2: # 4 bytes
            result = unpack('>f', data)[0]
        elif length == 3: # 8 bytes
            result = unpack('>d', data)[0]
        else:
            raise InvalidPlistException("Unknown real of length %d bytes" % to_read)
        return result
    
    def readRefs(self, count):    
        refs = []
        i = 0
        while i < count:
            fragment = self.contents[self.currentOffset:self.currentOffset+self.trailer.objectRefSize]
            ref = self.getSizedInteger(fragment, len(fragment))
            refs.append(ref)
            self.currentOffset += self.trailer.objectRefSize
            i += 1
        return refs
    
    def readArray(self, count):
        result = []
        values = self.readRefs(count)
        i = 0
        while i < len(values):
            self.setCurrentOffsetToObjectNumber(values[i])
            value = self.readObject()
            result.append(value)
            i += 1
        return result
    
    def readDict(self, count):
        result = {}
        keys = self.readRefs(count)
        values = self.readRefs(count)
        i = 0
        while i < len(keys):
            self.setCurrentOffsetToObjectNumber(keys[i])
            key = self.readObject()
            self.setCurrentOffsetToObjectNumber(values[i])
            value = self.readObject()
            result[key] = value
            i += 1
        return result
    
    def readAsciiString(self, length):
        result = unpack("!%ds" % length, self.contents[self.currentOffset:self.currentOffset+length])[0]
        self.currentOffset += length
        return result
    
    def readUnicode(self, length):
        data = self.contents[self.currentOffset:self.currentOffset+length*2]
        data = unpack(">%ds" % (length*2), data)[0]
        self.currentOffset += length * 2
        return data.decode('utf-16-be')
    
    def readDate(self):
        result = unpack(">d", self.contents[self.currentOffset:self.currentOffset+8])[0]
        result = datetime.datetime.utcfromtimestamp(result + 978307200)
        self.currentOffset += 8
        return result
    
    def readData(self, length):
        result = self.contents[self.currentOffset:self.currentOffset+length]
        self.currentOffset += length
        return result
    
    def readUid(self, length):
        return Uid(self.readInteger(length+1))
    
    def getSizedInteger(self, data, intSize):
        result = 0
        i = 0
        d_read = ''
        while i < intSize:
            d_read += bin(unpack('!B', data[i])[0])
            result += (result << 8) + unpack('!B', data[i])[0]
            i += 1
        return result
