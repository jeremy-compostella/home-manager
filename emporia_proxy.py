#!/usr/bin/env python3
#
# Author: Jeremy Compostella <jeremy.compostella@gmail.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer
#      in the documentation and/or other materials provided with the
#      distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

import os

from datetime import datetime, timedelta
from multiprocessing.connection import Listener

from pyemvue.enums import Scale

from sensor import MyVue2
from tools import debug, init

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')

    vue = MyVue2(config['Emporia'])
    cache = {}
    listener = Listener((config['EmporiaProxy']['host'],
                         int(config['EmporiaProxy']['port'])))
    debug("... is now ready to run")
    while True:
        conn = listener.accept()
        scale = conn.recv()
        data = {}
        if scale not in cache or datetime.now() > cache[scale]['expiration_time']:
            try:
                debug('Reading with scale %s' % scale)
                data = vue.read(scale)
            except:
                debug('Failed to read from Emporia servers')
                pass
            if data:
                cache[scale] = { 'data':data }
                if scale == Scale.SECOND.value:
                    cache[scale]['expiration_time'] = \
                        datetime.now() + timedelta(seconds=15)
                else:
                    cache[scale]['expiration_time'] = \
                        datetime.now().replace(second=0, microsecond=0) + \
                        timedelta(minutes=1)
        else:
            data = cache[scale]['data']
        conn.send(data)
        conn.close()

if __name__ == "__main__":
    main()
