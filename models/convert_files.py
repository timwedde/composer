#!/usr/bin/env python3

### System ##
import os
import sys
import argparse
import subprocess
from glob import glob
from shutil import rmtree
from os.path import join, dirname, exists
from signal import signal, SIGINT, SIG_IGN
from multiprocessing import cpu_count, Pool
from os import mkdir, makedirs, remove, listdir

### Display ###
from tqdm import tqdm

### Audio ###
from pydub import AudioSegment


def check(args):
    if not exists(args.input_dir):
        print("The input directory does not exist!")
        sys.exit(1)
    if exists(args.output_dir):
        print("The output directory exists. Do you want to overwrite it?")
        result = input("[y]es/[n]o: ").lower()
        if not result in ["y", "yes"]:
            print("Aborted")
            sys.exit(0)
        rmtree(args.output_dir)
    os.makedirs(args.output_dir, exist_ok=True)


def transform(file):
    path = file.split(os.sep)
    sub_path = os.sep.join(path[1:-1])
    filename, ext = os.path.splitext(os.path.basename(file))

    target_filepath = os.path.join(args.output_dir, sub_path)
    os.makedirs(target_filepath, exist_ok=True)
    target_filename_wav = os.path.join(target_filepath, filename) + ".tmp.wav"
    target_filename_mp3 = os.path.join(target_filepath, filename) + ".mp3"
    subprocess.run(["fluidsynth", "-F", target_filename_wav, "general_user.sf2", file], stdout=subprocess.PIPE)
    wav_file = AudioSegment.from_wav(target_filename_wav)
    wav_file.export(target_filename_mp3, format="mp3")
    os.remove(target_filename_wav)


def main(args):
    files = glob(os.path.join(args.input_dir, "**", "*.mid"), recursive=True)
    for output in tqdm(worker_pool.imap_unordered(transform, files), total=len(files), unit="files"):
        pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", type=str, dest="input_dir", required=True,
                        metavar="dir", help="(required) The directory containing the input data")
    parser.add_argument("-s", "--soundfont", type=str, dest="soundfont", required=True,
                        metavar="file", help="(required) The soundfont to render with")
    parser.add_argument("-o", "--output", type=str, dest="output_dir", required=True,
                        metavar="dir", help="(required) The directory to output data to")
    parser.add_argument("-t", "--threads", type=int, dest="num_threads", default=cpu_count(),
                        metavar="N", help="The amount of threads to use (default: {})".format(cpu_count()))
    args = parser.parse_args()

    original_sigint_handler = signal(SIGINT, SIG_IGN)
    worker_pool = Pool(args.num_threads)
    signal(SIGINT, original_sigint_handler)

    check(args)

    try:
        main(args)
    except KeyboardInterrupt:
        print("\nReceived SIGINT, terminating...")
        worker_pool.terminate()
    else:
        worker_pool.close()

    worker_pool.join()
