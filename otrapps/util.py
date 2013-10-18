#
# Copyright 2008 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Utility functions for keyczar package modified to use standard base64
format that is commonly used for OTR keys.

@author: arkajit.dey@gmail.com (Arkajit Dey)
@author: hans@eds.org (Hans-Christoph Steiner)
"""

from __future__ import print_function
import base64
import math
import os
import psutil
import re
import signal
import sys
try:
    # Import hashlib if Python >= 2.5
    from hashlib import sha1
except ImportError:
    from sha import sha as sha1

from pyasn1.codec.der import decoder
from pyasn1.codec.der import encoder
from pyasn1.type import univ

from potr.utils import bytes_to_long
from potr.compatcrypto import DSAKey

import errors


# gracefully handle it when pymtp doesn't exist
class MTPDummy():
    def detect_devices(self):
        return []
try:
    import pymtp
    mtp = pymtp.MTP()
except:
    mtp = MTPDummy()
# GNOME GVFS mount point for MTP devices
mtp.gvfs_mountpoint = os.path.join(os.getenv('HOME'), '.gvfs', 'mtp')


HLEN = sha1().digest_size  # length of the hash output

#RSAPrivateKey ::= SEQUENCE {
#  version Version,
#  modulus INTEGER, -- n
#  publicExponent INTEGER, -- e
#  privateExponent INTEGER, -- d
#  prime1 INTEGER, -- p
#  prime2 INTEGER, -- q
#  exponent1 INTEGER, -- d mod (p-1)
#  exponent2 INTEGER, -- d mod (q-1)
#  coefficient INTEGER -- (inverse of q) mod p }
#
#Version ::= INTEGER
RSA_OID = univ.ObjectIdentifier('1.2.840.113549.1.1.1')
RSA_PARAMS = ['n', 'e', 'd', 'p', 'q', 'dp', 'dq', 'invq']
DSA_OID = univ.ObjectIdentifier('1.2.840.10040.4.1')
DSA_PARAMS = ['p', 'q', 'g']  # only algorithm params, not public/private keys
SHA1RSA_OID = univ.ObjectIdentifier('1.2.840.113549.1.1.5')
SHA1_OID = univ.ObjectIdentifier('1.3.14.3.2.26')

def ASN1Sequence(*vals):
    seq = univ.Sequence()
    for i in range(len(vals)):
        seq.setComponentByPosition(i, vals[i])
    return seq

def ParseASN1Sequence(seq):
    return [seq.getComponentByPosition(i) for i in range(len(seq))]

#PrivateKeyInfo ::= SEQUENCE {
#  version Version,
#
#  privateKeyAlgorithm PrivateKeyAlgorithmIdentifier,
#  privateKey PrivateKey,
#  attributes [0] IMPLICIT Attributes OPTIONAL }
#
#Version ::= INTEGER
#
#PrivateKeyAlgorithmIdentifier ::= AlgorithmIdentifier
#
#PrivateKey ::= OCTET STRING
#
#Attributes ::= SET OF Attribute
def ParsePkcs8(pkcs8):
    seq = ParseASN1Sequence(decoder.decode(Decode(pkcs8))[0])
    if len(seq) != 3:  # need three fields in PrivateKeyInfo
        raise errors.KeyczarError("Illegal PKCS8 String.")
    version = int(seq[0])
    if version != 0:
        raise errors.KeyczarError("Unrecognized PKCS8 Version")
    [oid, alg_params] = ParseASN1Sequence(seq[1])
    key = decoder.decode(seq[2])[0]
    # Component 2 is an OCTET STRING which is further decoded
    params = {}
    if oid == RSA_OID:
        key = ParseASN1Sequence(key)
        version = int(key[0])
        if version != 0:
            raise errors.KeyczarError("Unrecognized RSA Private Key Version")
        for i in range(len(RSA_PARAMS)):
            params[RSA_PARAMS[i]] = long(key[i+1])
    elif oid == DSA_OID:
        alg_params = ParseASN1Sequence(alg_params)
        for i in range(len(DSA_PARAMS)):
            params[DSA_PARAMS[i]] = long(alg_params[i])
        params['x'] = long(key)
    else:
        raise errors.KeyczarError("Unrecognized AlgorithmIdentifier: not RSA/DSA")
    return params

def ExportRsaPkcs8(params):
    oid = ASN1Sequence(RSA_OID, univ.Null())
    key = univ.Sequence().setComponentByPosition(0, univ.Integer(0))  # version
    for i in range(len(RSA_PARAMS)):
        key.setComponentByPosition(i+1, univ.Integer(params[RSA_PARAMS[i]]))
    octkey = encoder.encode(key)
    seq = ASN1Sequence(univ.Integer(0), oid, univ.OctetString(octkey))
    return Encode(encoder.encode(seq))

def ExportDsaPkcs8(params):
    alg_params = univ.Sequence()
    for i in range(len(DSA_PARAMS)):
        alg_params.setComponentByPosition(i, univ.Integer(params[DSA_PARAMS[i]]))
    oid = ASN1Sequence(DSA_OID, alg_params)
    octkey = encoder.encode(univ.Integer(params['x']))
    seq = ASN1Sequence(univ.Integer(0), oid, univ.OctetString(octkey))
    return Encode(encoder.encode(seq))

#NOTE: not full X.509 certificate, just public key info
#SubjectPublicKeyInfo  ::=  SEQUENCE  {
#        algorithm            AlgorithmIdentifier,
#        subjectPublicKey     BIT STRING  }
def ParseX509(x509):
    seq = ParseASN1Sequence(decoder.decode(Decode(x509))[0])
    if len(seq) != 2:  # need two fields in SubjectPublicKeyInfo
        raise errors.KeyczarError("Illegal X.509 String.")
    [oid, alg_params] = ParseASN1Sequence(seq[0])
    binstring = seq[1].prettyPrint()[1:-2]
    pubkey = decoder.decode(univ.OctetString(BinToBytes(binstring.replace("'", ""))))[0]
    # Component 1 should be a BIT STRING, get raw bits by discarding extra chars,
    # then convert to OCTET STRING which can be ASN.1 decoded
    params = {}
    if oid == RSA_OID:
        [params['n'], params['e']] = [long(x) for x in ParseASN1Sequence(pubkey)]
    elif oid == DSA_OID:
        vals = [long(x) for x in ParseASN1Sequence(alg_params)]
        for i in range(len(DSA_PARAMS)):
            params[DSA_PARAMS[i]] = vals[i]
        params['y'] = long(pubkey)
    else:
        raise errors.KeyczarError("Unrecognized AlgorithmIdentifier: not RSA/DSA")
    return params

def ExportRsaX509(params):
    oid = ASN1Sequence(RSA_OID, univ.Null())
    key = ASN1Sequence(univ.Integer(params['n']), univ.Integer(params['e']))
    binkey = BytesToBin(encoder.encode(key))
    pubkey = univ.BitString("'%s'B" % binkey)  # needs to be a BIT STRING
    seq = ASN1Sequence(oid, pubkey)
    return Encode(encoder.encode(seq))

def ExportDsaX509(params):
    alg_params = ASN1Sequence(univ.Integer(params['p']),
                              univ.Integer(params['q']),
                              univ.Integer(params['g']))
    oid = ASN1Sequence(DSA_OID, alg_params)
    binkey = BytesToBin(encoder.encode(univ.Integer(params['y'])))
    pubkey = univ.BitString("'%s'B" % binkey)  # needs to be a BIT STRING
    seq = ASN1Sequence(oid, pubkey)
    return Encode(encoder.encode(seq))

def MakeDsaSig(r, s):
    """
    Given the raw parameters of a DSA signature, return a Base64 signature.

    @param r: parameter r of DSA signature
    @type r: long int

    @param s: parameter s of DSA signature
    @type s: long int

    @return: raw byte string formatted as an ASN.1 sequence of r and s
    @rtype: string
    """
    seq = ASN1Sequence(univ.Integer(r), univ.Integer(s))
    return encoder.encode(seq)

def ParseDsaSig(sig):
    """
    Given a raw byte string, return tuple of DSA signature parameters.

    @param sig: byte string of ASN.1 representation
    @type sig: string

    @return: parameters r, s as a tuple
    @rtype: tuple

    @raise KeyczarErrror: if the DSA signature format is invalid
    """
    seq = decoder.decode(sig)[0]
    if len(seq) != 2:
        raise errors.KeyczarError("Illegal DSA signature.")
    r = long(seq.getComponentByPosition(0))
    s = long(seq.getComponentByPosition(1))
    return (r, s)

def MakeEmsaMessage(msg, modulus_size):
    """Algorithm EMSA_PKCS1-v1_5 from PKCS 1 version 2"""
    magic_sha1_header = [0x30, 0x21, 0x30, 0x9, 0x6, 0x5, 0x2b, 0xe, 0x3, 0x2,
                         0x1a, 0x5, 0x0, 0x4, 0x14]
    encoded = "".join([chr(c) for c in magic_sha1_header]) + Hash(msg)
    pad_string = chr(0xFF) * (modulus_size / 8 - len(encoded) - 3)
    return chr(1) + pad_string + chr(0) + encoded

def BinToBytes(bits):
    """Convert bit string to byte string."""
    bits = _PadByte(bits)
    octets = [bits[8*i:8*(i+1)] for i in range(len(bits)/8)]
    bytes = [chr(int(x, 2)) for x in octets]
    return "".join(bytes)

def BytesToBin(bytes):
    """Convert byte string to bit string."""
    return "".join([_PadByte(IntToBin(ord(byte))) for byte in bytes])

def _PadByte(bits):
    """Pad a string of bits with zeros to make its length a multiple of 8."""
    r = len(bits) % 8
    return ((8-r) % 8)*'0' + bits

def IntToBin(n):
    if n == 0 or n == 1:
        return str(n)
    elif n % 2 == 0:
        return IntToBin(n/2) + "0"
    else:
        return IntToBin(n/2) + "1"

def BigIntToBytes(n):
    """Return a big-endian byte string representation of an arbitrary length n."""
    chars = []
    while (n > 0):
        chars.append(chr(n % 256))
        n = n >> 8
    chars.reverse()
    return "".join(chars)

def IntToBytes(n):
    """Return byte string of 4 big-endian ordered bytes representing n."""
    bytes = [m % 256 for m in [n >> 24, n >> 16, n >> 8, n]]
    return "".join([chr(b) for b in bytes])  # byte array to byte string

def BytesToLong(bytes):
    l = len(bytes)
    return long(sum([ord(bytes[i]) * 256**(l - 1 - i) for i in range(l)]))

def Xor(a, b):
    """Return a ^ b as a byte string where a and b are byte strings."""
    # pad shorter byte string with zeros to make length equal
    m = max(len(a), len(b))
    if m > len(a):
        a = PadBytes(a, m - len(a))
    elif m > len(b):
        b = PadBytes(b, m - len(b))
    x = [ord(c) for c in a]
    y = [ord(c) for c in b]
    z = [chr(x[i] ^ y[i]) for i in range(m)]
    return "".join(z)

def PadBytes(bytes, n):
    """Prepend a byte string with n zero bytes."""
    return n * '\x00' + bytes

def TrimBytes(bytes):
    """Trim leading zero bytes."""
    trimmed = bytes.lstrip(chr(0))
    if trimmed == "":  # was a string of all zero bytes
        return chr(0)
    else:
        return trimmed

def RandBytes(n):
    """Return n random bytes."""
    # This function requires at least Python 2.4.
    return os.urandom(n)

def Hash(*inputs):
    """Return a SHA-1 hash over a variable number of inputs."""
    md = sha1()
    for i in inputs:
        md.update(i)
    return md.digest()

def PrefixHash(*inputs):
    """Return a SHA-1 hash over a variable number of inputs."""
    md = sha1()
    for i in inputs:
        md.update(IntToBytes(len(i)))
        md.update(i)
    return md.digest()


def Encode(s):
    """
    Return Base64 encoding of s.

    @param s: string to encode as Base64
    @type s: string

    @return: Base64 representation of s.
    @rtype: string
    """
    return base64.b64encode(str(s))


def Decode(s):
    """
    Return decoded version of given Base64 string. Ignore whitespace.

    @param s: Base64 string to decode
    @type s: string

    @return: original string that was encoded as Base64
    @rtype: string

    @raise Base64DecodingError: If length of string (ignoring whitespace) is one
      more than a multiple of four.
    """
    s = str(s.replace(" ", ""))  # kill whitespace, make string (not unicode)
    d = len(s) % 4
    if d == 1:
        raise errors.Base64DecodingError()
    elif d == 2:
        s += "=="
    elif d == 3:
        s += "="
    return base64.b64decode(s)

def WriteFile(data, loc):
    """
    Writes data to file at given location.

    @param data: contents to be written to file
    @type data: string

    @param loc: name of file to write to
    @type loc: string

    @raise KeyczarError: if unable to write to file because of IOError
    """
    try:
        f = open(loc, "w")
        f.write(data)
        f.close()
    except IOError:
        raise errors.KeyczarError("Unable to write to file %s." % loc)

def ReadFile(loc):
    """
    Read data from file at given location.

    @param loc: name of file to read from
    @type loc: string

    @return: contents of the file
    @rtype: string

    @raise KeyczarError: if unable to read from file because of IOError
    """
    try:
        return open(loc).read()
    except IOError:
        raise errors.KeyczarError("Unable to read file %s." % loc)

def MGF(seed, mlen):
    """
    Mask Generation Function (MGF1) with SHA-1 as hash.

    @param seed: used to generate mask, a byte string
    @type seed: string

    @param mlen: desired length of mask
    @type mlen: integer

    @return: mask, byte string of length mlen
    @rtype: string

    @raise KeyczarError: if mask length too long, > 2^32 * hash_length
    """
    if mlen > 2**32 * HLEN:
        raise errors.KeyczarError("MGF1 mask length too long.")
    output = ""
    for i in range(int(math.ceil(mlen / float(HLEN)))):
        output += Hash(seed, IntToBytes(i))
    return output[:mlen]


def fingerprint(key):
    '''generate the human readable form of the fingerprint as used in OTR'''
    return '{0:040x}'.format(bytes_to_long(DSAKey(key).fingerprint()))


def check_and_set(key, k, v):
    '''
    Check if a key is already in the keydict, check its contents against the
    supplied value.  If the key does not exist, then create a new entry in
    keydict.  If the key exists and has a different value, throw an exception.
    '''
    if k in key:
        if key[k] != v:
            if 'name' in key:
                name = key['name']
            else:
                name = '(unknown)'
            # this should be an Exception so that the GUI can catch it to handle it
            print('"' + k + '" values for "' + name + '" did not match: \n\t"' + str(key[k])
                            + '" != "' + str(v) + '"')
    else:
        key[k] = v

def merge_keys(key1, key2):
    '''merge the second key data into the first, checking for conflicts'''
    for k, v in key2.items():
        check_and_set(key1, k, v)


def merge_keydicts(kd1, kd2):
    '''
    given two keydicts, merge the second one into the first one and report errors
    '''
    for name, key in kd2.items():
        if name in kd1:
            merge_keys(kd1[name], key)
        else:
            kd1[name] = key


def which_apps_are_running(apps):
    '''
    Check the process list to see if any of the specified apps are running.
    It returns a tuple of running apps.
    '''
    running = []
    for pid in psutil.get_pid_list():
        try:
            p = psutil.Process(pid)
        except Exception as e:
            print(e)
            continue
        for app in apps:
            if app == p.name:
                running.append(app)
                print('found: ' + p.name)
            else:
                r = re.compile('.*' + app + '.*', re.IGNORECASE)
                try:
                    for arg in p.cmdline:
                        m = r.match(arg)
                        if m and (p.name == 'python' or p.name == 'java'):
                            running.append(app)
                            break
                except:
                    pass
    return tuple(running)


def killall(app):
    '''
    terminates all instances of an app
    '''
    running = []
    for pid in psutil.get_pid_list():
        p = psutil.Process(pid)
        if app == p.name:
            print('killing: ' + p.name)
            os.kill(pid, signal.SIGKILL)
        else:
            r = re.compile('.*' + app + '.*', re.IGNORECASE)
            try:
                for arg in p.cmdline:
                    m = r.match(arg)
                    if m and (p.name == 'python' or p.name == 'java'):
                        print('killing: ' + p.name)
                        os.kill(pid, signal.SIGKILL)
                        break
            except:
                pass


def _fullcopy(src, dst):
    '''
    A simple full file copy that ignores perms, since MTP doesn't play well
    with them.  shutil.copy() tries to dup perms...
    '''
    with open(src) as f:
        content = f.readlines()
    with open(dst, 'w') as f:
        f.writelines(content)


def make_conffile_backup(filename):
    '''makes a in-place backup of the given config file name'''
    realpath = os.path.realpath(filename) # eliminate symlinks
    s = os.stat(realpath)
    timestamp = s.st_mtime
    _fullcopy(realpath, realpath + '.' +  str(timestamp))


def mtp_is_mounted():
    '''checks if an MTP device is mounted, i.e. an Android 4.x device'''
    if sys.platform == 'win32':
        # TODO implement, probably using wmdlib
        pass
    elif os.path.exists(mtp.gvfs_mountpoint):
        # this assumes that gvfs is mounting the MTP device.  gvfs is
        # part of GNOME, but is probably included in other systems too
        mtp.devicename = mtp.gvfs_mountpoint
        return os.path.exists(mtp.gvfs_mountpoint)
    else:
        # if all else fails, try libmtp/pymtp, it works on Mac OS X at least!
        try:
            devices = mtp.detect_devices()
            if len(devices) > 0:
                e = devices[0].device_entry
                mtp.devicename = e.vendor + ' ' + e.product
                return True
            else:
                mtp.devicename = ''
                return False
        except Exception as e:
            print('except ' + str(e))
            return False


def get_mtp_mount_path():
    '''copy a file to the relevant MTP mount'''
    if sys.platform == 'win32':
        # TODO implement!
        pass
    elif os.path.exists(mtp.gvfs_mountpoint):
        foundit = False
        # this assumes that gvfs is mounting the MTP device
        if os.path.isdir(os.path.join(mtp.gvfs_mountpoint, 'Internal storage')):
            mtpdir = os.path.join(mtp.gvfs_mountpoint, 'Internal storage')
            foundit = True
        elif os.path.isdir(os.path.join(mtp.gvfs_mountpoint, 'SD card')):
            mtpdir = os.path.join(mtp.gvfs_mountpoint, 'SD card')
            foundit = True
        else:
            # if no standard names, try the first dir we find
            files = os.listdir(mtp.gvfs_mountpoint)
            if len(files) > 0:
                for f in files:
                    fp = os.path.join(mtp.gvfs_mountpoint, f)
                    if os.path.isdir(fp):
                        mtpdir = fp
                        foundit = True
        if foundit:
            return mtpdir
    else:
        # For systems without native support for mounting MTP, we have to take
        # a different approach, and transfer the file using MTP. A temp dir is
        # created, and otr_keystore is written there, then copied to the
        # device using pymtp.
        import tempfile
        return tempfile.mkdtemp(prefix='.keysync-')


#------------------------------------------------------------------------------#
# for testing from the command line:
def main(argv):
    import pprint

    key = dict()
    key['name'] = 'key'
    key['test'] = 'yes, testing'
    try:
        check_and_set(key, 'test', 'no, this should break')
    except Exception as e:
        print('Exception: ', end=' ')
        print(e)
    print(key['test'])
    check_and_set(key, 'test', 'yes, testing')
    print(key['test'])
    check_and_set(key, 'new', 'this is a new value')

    key2 = dict()
    key2['name'] = 'key'
    key2['yat'] = 'yet another test';
    merge_keys(key, key2)
    print('key: ', end=' ')
    pprint.pprint(key)

    # now make trouble again
    key2['test'] = 'yet another breakage'
    try:
        merge_keys(key, key2)
    except Exception as e:
        print('Exception: ', end=' ')
        print(e)

    # now let's try dicts of dicts aka 'keydict'
    keydict = dict()
    keydict['key'] = key
    key3 = dict()
    key3['name'] = 'key'
    key3['protocol'] = 'prpl-jabber'
    key4 = dict()
    key4['name'] = 'key4'
    key4['protocol'] = 'prpl-jabber'
    key4['fingerprint'] = 'gotone'
    key4['teststate'] = 'this one should not be merged'
    key5 = dict()
    key5['name'] = 'key'
    key5['protocol'] = 'prpl-jabber'
    key5['moreinfo'] = 'even more!'
    keydict2 = dict()
    keydict2['key'] = key3
    keydict2['key'] = key5
    keydict2['key4'] = key4
    merge_keydicts(keydict, keydict2)
    pprint.pprint(keydict)

    # one more test
    print('---------------------------')
    key6 = dict()
    key6['name'] = 'key'
    keydict3 = dict()
    keydict3['key'] = key6
    pprint.pprint(keydict3['key'])
    merge_keys(keydict3['key'], key3)
    pprint.pprint(keydict3['key'])
    merge_keys(keydict3['key'], key5)
    pprint.pprint(keydict3['key'])

    sys.path.insert(0, os.path.abspath('..'))
    import otrapps
    print('\n---------------------------')
    print('Which supported apps are currently running:')
    print((which_apps_are_running(otrapps.__all__)))

    print('\n---------------------------')
    print('make backup conf file: ')
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix='.keysync-util-test-')
    testfile = os.path.join(tmpdir, 'keysync-util-conffile-backup-test')
    with open(testfile, 'w') as f:
        f.write('this is just a test!\n')
    make_conffile_backup(testfile)
    print('Backed up "%s"' % testfile)

    if sys.platform == 'darwin' and  mtp_is_mounted():
        print('\n---------------------------')
        print('MTP is mounted here:', end=' ')
        print(get_mtp_mount_path())



if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
