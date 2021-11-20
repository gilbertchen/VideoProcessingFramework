#
# Copyright 2019 NVIDIA Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# Starting from Python 3.8 DLL search policy has changed.
# We need to add path to CUDA DLLs explicitly.
import sys
import os
import threading

if os.name == 'nt':
    # Add CUDA_PATH env variable
    cuda_path = os.environ["CUDA_PATH"]
    if cuda_path:
        os.add_dll_directory(cuda_path)
    else:
        print("CUDA_PATH environment variable is not set.", file=sys.stderr)
        print("Can't set CUDA DLLs search path.", file=sys.stderr)
        exit(1)

    # Add PATH as well for minor CUDA releases
    sys_path = os.environ["PATH"]
    if sys_path:
        paths = sys_path.split(';')
        for path in paths:
            if os.path.isdir(path):
                os.add_dll_directory(path)
    else:
        print("PATH environment variable is not set.", file=sys.stderr)
        exit(1)

import PyNvCodec as nvc
from enum import Enum
import numpy as np


import ffmpeg
import subprocess
from threading import Thread
import uuid


class Client(Thread):
    def __init__(self, url, name, gpu_id) -> None:
        Thread.__init__(self)

        # self.args = (ffmpeg
        #              .input(url)
        #              .output('pipe:', vcodec='copy', **{'bsf:v': 'h264_mp4toannexb,dump_extra=all'}, format='h264')
        #              .compile())

        # self.proc = subprocess.Popen(self.args, stdout=subprocess.PIPE)
        cmd = [
            'ffmpeg',
            '-loglevel',    'quiet',
            '-i',           url,
            '-c:v',         'copy',
            '-bsf:v',       'h264_mp4toannexb,dump_extra=all',
            '-f',           'h264',
            'pipe:1'
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

        nvdmx = nvc.PyFFmpegDemuxer(url, {})
        self.w = nvdmx.Width()
        self.h = nvdmx.Height()
        self.f = nvdmx.Format()
        self.c = nvdmx.Codec()
        self.g = gpu_id

        self.nvdec = nvc.PyNvDecoder(self.w, self.h, self.f, self.c, self.g)
        self.name = name

    def run(self):
        # Amount of bytes we read from pipe first time.
        read_size = 4096
        # Total bytes read and total frames decded - to get average data rate
        rt = 0
        fd = 0

        # Main decoding loop, will not flush intentionally because don't know the
        # amount of frames available via RTSP.
        while True:
            # Pipe read underflow protection
            if not read_size:
                read_size = int(rt / fd)
                # Counter overflow protection
                rt = read_size
                fd = 1

            # Read data.
            # Amount doesn't really matter, will be updated later on during decode.
            bits = self.proc.stdout.read(read_size)
            if not len(bits):
                print("Can't read data from pipe\n")
                break
            else:
                rt += len(bits)

            # Decode
            enc_packet = np.frombuffer(buffer=bits, dtype=np.uint8)
            pkt_data = nvc.PacketData()
            try:
                surf = self.nvdec.DecodeSurfaceFromPacket(enc_packet, pkt_data)

                if not surf.Empty():
                    fd += 1
                    # Shifts towards underflow to avoid increasing vRAM consumption.
                    if pkt_data.bsl < read_size:
                        read_size = pkt_data.bsl

            # Handle HW in simplest possible way by decoder respawn
            except nvc.HwResetException:
                self.nvdec = nvc.PyNvDecoder(
                    self.w, self.h, self.f, self.c, self.g)
                continue


if __name__ == "__main__":
    print("This sample decodes multiple H.264 RTSP videos in parallel on given GPU.")
    print("Usage: SampleDecodeRTSP.py $gpu_id $url1 ... $urlN .")

    if(len(sys.argv) < 3):
        print("Provide gpu ID and input URLs.")
        exit(1)

    gpuID = int(sys.argv[1])
    urls = []

    for i in range(2, len(sys.argv)):
        urls.append(sys.argv[i])

    pool = []
    for url in urls:
        client = Client(url, str(uuid.uuid4()), gpuID)
        client.start()
        pool.append(client)

    for client in pool:
        client.join()
