#!/usr/bin/python
"""
This script takes an input and ouput directory as arguments and will
compile all python files in the input directory and place the compiled
python in the output directory.
"""

import os
import sys
import time
import argparse
import py_compile
import traceback

def __make_parser():
    p = argparse.ArgumentParser(description='Given an input and output directory this will compile all .py files in the input direcotry and place the compiled outputs in the output direcotry')
    p.add_argument('-v', '--verbose', action='store_true', help='Verbose output', required = False)
    p.add_argument('-i', '--input-dir', type=str, help='path to the input direcotry', required = True)
    p.add_argument('-o', '--output-dir', type=str, help='path the the output directory', required = True)
    p.add_argument('-c', '--create-output-dir', action='store_true', help='create the output directory if it does not exist', required = False)
    return p


def get_outdir(dirpath, input_dir, output_dir):
    outdir = output_dir
    if (dirpath != input_dir):
        if (dirpath.startswith(input_dir)):
            subdir = dirpath[len(input_dir) + len(os.path.sep):]
            outdir = os.path.join(outdir, subdir)
            if not os.path.exists(outdir):
                os.makedirs(outdir)
        else:
            raise Exception("Error invalid path {}".format(dirpath))
            
    return outdir


def __main(argv):
    parser = __make_parser()
    settings = parser.parse_args(argv[1:])
    
    if (not os.path.isdir(settings.input_dir)):
        sys.stderr.write("Error input directory {} does not exist\n".format(settings.input_dir))
        sys.exit(1)
    if (settings.create_output_dir):
        if not os.path.exists(settings.output_dir):
            os.makedirs(settings.output_dir)
    else:
        if (not os.path.isdir(settings.output_dir)):
            sys.stderr.write("Error output directory {} does not exist\n".format(settings.output_dir))
            sys.exit(1)
    
    settings.input_dir = os.path.abspath(settings.input_dir)
    settings.output_dir = os.path.abspath(settings.output_dir)
    
    for (dirpath, dirnames, filenames) in os.walk(settings.input_dir):
        for f in filenames:
            if (f.endswith(os.path.extsep + 'py')):
                try:
                    outdir = get_outdir(dirpath, settings.input_dir, settings.output_dir)
                except:
                    sys.stderr.write("Error cannot create output directory")
                    sys.exit(1)
                outfile = os.path.join(outdir, f + 'c')
                infile = os.path.join(dirpath, f)
                if (settings.verbose):
                    print 'Compiling file {} to output {}'.format(infile, outfile)
                try:
                    py_compile.compile(infile, outfile, doraise=True)
                except Exception as e:
                    print e
                    sys.stderr.write("Error compiling file {}\n".format(infile))
                    sys.exit(1)
    
    sys.exit(0)

if __name__ == "__main__":
    __main(sys.argv)

__doc__ += __make_parser().format_help()
