#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import platform
import sys
import plistlib
import re

import pgpdump

from otr_private_key import OtrPrivateKeys
from otr_fingerprints import OtrFingerprints

class GPGProperties():

    path = os.path.expanduser('~/.gnupg')
    secring = 'secring.gpg'

    @staticmethod
    def parse(settingsdir=None):
        if settingsdir == None:
            settingsdir = GPGProperties.path

        secring_file = os.path.join(settingsdir, GPGProperties.secring)
        rawdata = GPGProperties.load_data(secring_file)
        data = pgpdump.BinaryData(rawdata)
        packets = list(data.packets())

        # TODO parse uids for standard otr key tag,
        # also TODO invent that standard

        # possible regex for uid otr detect:
        # finds "otr: keyid"
        #    otr_key = re.search(r'otr\\x3a(.{8})', uid ).group(1)
        # until then we assume all dsa private subkeys are otr keys

        keydict = dict()
        for packet in packets:
            if isinstance(packet, pgpdump.packet.SecretSubkeyPacket):
                if packet.pub_algorithm_type == "dsa":
                    values = dict()
                    values['p'] = packet.prime
                    values['q'] = packet.group_order
                    values['g'] = packet.group_gen
                    values['y'] = packet.key_value
                    values['x'] = packet.exponent_x
                    keydict[packet.key_id] = values
        return keydict

    @staticmethod
    def write(keys, savedir):
        pass

    @staticmethod
    def load_data(filename):
        with open(filename, 'rb') as fileobj:
            data = fileobj.read()
        return data


if __name__ == '__main__':

    import pprint

    print 'GPG stores its files in ' + GPGProperties.path

    if len(sys.argv) == 2:
        settingsdir = sys.argv[1]
    else:
        settingsdir = '../tests/gpg'

    l = GPGProperties.parse(settingsdir)
    pprint.pprint(l)
