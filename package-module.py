#!/usr/bin/python
# This python script packages a module as a .tgz or a .mod.  The .mod file is
# an encrypted file that is signed with a signing private key.
# You will have to install the python-crypto package if it is not already installed.
# Useful resources:
# https://www.dlitz.net/software/pycrypto/api/current/Crypto.Signature.PKCS1_v1_5-module.html
# https://www.dlitz.net/software/pycrypto/api/current/Crypto.PublicKey.RSA._RSAobj-class.html#sign
# https://www.dlitz.net/software/pycrypto/api/current/Crypto.Cipher.PKCS1_OAEP-module.html
# http://www.laurentluce.com/posts/python-and-cryptography-with-pycrypto/
#
# The arguments package-module.py accepts include:
# -m the module directory to package [REQUIRED]
# -b the build number, replaces the last digit (patch number) of the version in module.json [OPTIONAL]
# -e the PUBLIC key with which to encrypt the module [OPTIONAL]
# -s the PRIVATE key with which to sign the module [OPTIONAL]
# -p include the python source instead of the compiled python (developer only) [OPTIONAL]
# --base specify that this is a base that is being packaged [OPTIONAL]
# -l specify that this is a legacy base build (pre v0.10) and to use base.json vice module.json [OPTIONAL]
#
# The .mod file has the following format:
# +------------------------+
# +       signature        +
# +      [512 bytes]       +
# +------------------------+
# + RSA encrypted password +
# +      [512 bytes]       +
# +------------------------+
# +   RSA encrypted salt   +
# +      [512 bytes]       +
# +------------------------+
# + RSA encrypted filename +
# +      [512 bytes]       +
# +------------------------+
# +      Symmetric Key     +
# +    encrypted package   +
# +      [module.pack]     +
# +------------------------+

import sys
import re
import argparse
import os
import json
import tarfile
import subprocess
import tempfile
import shutil
import fnmatch
import base64
import struct
import string
import random
import binascii


def make_tarfile(output_filename, source_dir):
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))


def get_git_hash(module_dir):
    wd = os.getcwd()
    os.chdir(module_dir)
    git_hash = None
    try:
        git_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'])
        git_hash = git_hash.strip('\n')
    except Exception:
        print 'Error not able to get git_hash'
    os.chdir(wd)
    return git_hash


def get_git_branch(module_dir):
    wd = os.getcwd()
    os.chdir(module_dir)
    git_branch = None
    try:
        git_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        git_branch = git_branch.strip('\n')
    except Exception as e:
        print 'Error not able to get git_branch'
        print e
    os.chdir(wd)
    return git_branch


def get_scripts_dir(module_dir, json_file):
    m_json = os.path.join(module_dir, json_file)
    print m_json
    json_data = open(m_json)
    data = json.load(json_data)
    json_data.close()

    if "scriptDir" in data:
        return data["scriptDir"]
    elif os.path.isdir(os.path.join(module_dir, 'MATDaemon')):
        return 'MATDaemon'
    elif os.path.isdir(os.path.join(module_dir, 'Scripts')):
        return 'Scripts'
    else:
        return None


def update_git_info(module_dir, json_file, git_hash, git_branch):
    m_json = os.path.join(module_dir, json_file)
    json_data = open(m_json)
    data = json.load(json_data)
    json_data.close()

    if git_hash is not None:
        data["git_short_hash"] = git_hash
        data["git_branch"] = git_branch
        with open(m_json, 'w') as outfile:
            json.dump(data, outfile, indent=2, separators=(',', ': '))


def update_display_name(module_dir, json_file):
    m_json = os.path.join(module_dir, json_file)
    json_data = open(m_json)
    data = json.load(json_data)
    json_data.close()

    if "displayName" in data:
        display_name = data["displayName"]
        data["displayName"] = display_name + " DEV"
        with open(m_json, 'w') as outfile:
            json.dump(data, outfile, indent=2, separators=(',', ': '))


def update_build_number(module_dir, json_file, build_number):
    m_json = os.path.join(module_dir, json_file)
    json_data = open(m_json)
    data = json.load(json_data)
    json_data.close()

    semver = data["version"].split(".")
    major = semver[0]
    minor = semver[1]

    data["version"] = major + "." + minor + "." + build_number
    with open(m_json, 'w') as outfile:
        json.dump(data, outfile, indent=2, separators=(',', ': '))


def update_version(module_dir, json_file, version, git_hash):
    m_json = os.path.join(module_dir, json_file)
    json_data = open(m_json)
    data = json.load(json_data)
    json_data.close()

    data["version"] = version
    if git_hash is not None:
        data["git_short_hash"] = git_hash

    with open(m_json, 'w') as outfile:
        json.dump(data, outfile, indent=2, separators=(',', ': '))


def create_build_dir(module_dir):
    tmpdir = tempfile.mkdtemp()
    tmpdir = os.path.join(tmpdir, os.path.basename(module_dir))
    os.makedirs(tmpdir)
    return tmpdir


def remove_build_dir(build_dir):
    shutil.rmtree(os.path.dirname(build_dir))


def copy_module_files(src, dst, exclude_files, exclude_dirs, symlinks=True):
    for (dirpath, _dirnames, filenames) in os.walk(src):
        dstdir = dst
        if (dirpath != src):
            if (dirpath.startswith(src)):
                subdir = dirpath[len(src) + len(os.path.sep):]
                skip = False
                subdir_folders = subdir.split(os.path.sep)
                for exclude_dir in exclude_dirs:
                    if exclude_dir in subdir_folders:
                        skip = True
                if skip:
                    continue
                dstdir = os.path.join(dstdir, subdir)
                if not os.path.exists(dstdir):
                    os.makedirs(dstdir)
            else:
                raise Exception("Error invalid path {}".format(dirpath))
        for filename in filenames:
            skip = False
            for exclude_file in exclude_files:
                if fnmatch.fnmatch(filename, exclude_file):
                    skip = True
            if not skip:
                srcname = os.path.join(dirpath, filename)
                dstname = os.path.join(dstdir, filename)
                if symlinks and os.path.islink(srcname):
                    linkto = os.readlink(srcname)
                    os.symlink(linkto, dstname)
                else:
                    shutil.copy(srcname, dstdir)


def get_has_npm_build(buildDir):
    # Create the package.json filepath
    packageJsonFilepath = os.path.join(buildDir, 'package.json')

    # Make sure the package.json file exists
    if not os.path.isfile(packageJsonFilepath):
        # No package.json therefore no npm build!
        return False

    # Open the package.json file
    packageData = open(packageJsonFilepath)
    # Read the package.json file
    packageJson = json.load(packageData)
    # Close the package.json file
    packageData.close()

    # Check if package.json has a 'scripts' property
    if 'scripts' not in packageJson:
        # No 'scripts' property, nothing to run
        return False

    # Get the 'scripts' property
    scripts = packageJson['scripts']

    # Check if the scripts property has a 'build' property
    if 'build' not in scripts:
        # No 'build' property, nothing to run
        return False

    # The 'build' property exists!
    return True


def run_npm_build(buildDir):
    my_env = os.environ.copy()
    # Set yarn cache directory for storing later
    yarnCacheFolder = os.path.join(buildDir, 'support', 'yarn-cache')
    os.makedirs(yarnCacheFolder)
    my_env['YARN_CACHE_FOLDER'] = yarnCacheFolder

    # Create arguments for running the build command
    args = ['npm', 'run', 'build']
    # Create a subprocess to run the build command, wait for it to complete
    ret = subprocess.call(args, cwd=buildDir, env=my_env)
    if 0 != ret:
        print 'Failed to run \'npm run build\''
        remove_build_dir(buildDir)
        sys.exit(2)


def run_pre_package_scripts(scripts, buildDir):
    print 'Scripts: ', scripts
    my_env = os.environ.copy()

    for script in scripts:
        print "Running " + script
        try:
            args = script.split(' ')
            ret = subprocess.call(args, cwd=buildDir, env=my_env)
            if 0 != ret:
                print 'Failed to run ', ret
        except Exception as e:
            print 'Failed to run script ', e


def pre_package_cleanup(build_dir):
    nodeModDir = os.path.join(build_dir, 'node_modules')
    testDir = os.path.join(build_dir, 'test')
    testCoverageDir = os.path.join(build_dir, 'coverage')
    if os.path.exists(nodeModDir):
        shutil.rmtree(nodeModDir)
    if os.path.exists(testDir):
        shutil.rmtree(testDir)
    if os.path.exists(testCoverageDir):
        shutil.rmtree(testCoverageDir)


def __make_parser():
    p = argparse.ArgumentParser(description='This packages a module (or the base) into a tar file')
    p.add_argument('-m', '--module-dir', type=str,
                   help='path to the module that you would like packaged', required=True)
    p.add_argument('-a', '--pre-package', action='append', dest='pre_package_scripts', default=[],
                   help='Optional script(s) that will be run just before the module is packaged into a tgz can be used \
                         to minifiy, or tweak modules')
    p.add_argument('-b', '--buildnum', type=str,
                   help='the build number to be placed in the json package information file, deprecated new builds \
                         should use version')
    p.add_argument('-g', '--git-branch',
                   help='branch name for the current build (needed for gitlab or jenkins builds) deprecated new builds \
                         should use version')
    p.add_argument('-e', '--encryptionkey', type=str, help='the public key used to encrypt the module')
    p.add_argument('-s', '--signingkey', type=str, help='the private key used to sign the module')
    p.add_argument('-p', '--include-python-source', action='store_true', help='include the python source in the build')
    p.add_argument('-d', '--dev', action='store_true', help='tag as development build')
    p.add_argument('--skip-apt-offline-bundles', action='store_true', help='skip generation of apt-offline bundles')
    p.add_argument('-P', '--python-paths', nargs='+', type=str,
                   help='path to folder(s) that you would like compiled python code for in addition to Scripts dir')
    p.add_argument('-v', '--version', type=str,
                   help='Version number to apply to this build this is the new method of version tracking and replaces \
                         git-branch and buildnum')
    return p


def __main(argv):
    parser = __make_parser()
    settings = parser.parse_args(argv[1:])
    MYDIR = os.path.dirname(os.path.realpath(__file__))

    if (not os.path.isdir(settings.module_dir)):
        sys.stderr.write('Error module dir is not a valid directory\n')
        sys.exit(1)

    json_file = 'module.json'

    settings.module_dir = os.path.abspath(settings.module_dir)

    build_dir = create_build_dir(settings.module_dir)

    script_dir = get_scripts_dir(settings.module_dir, json_file)

    if settings.python_paths is None:
        settings.python_paths = []
    if script_dir:
        settings.python_paths.append(script_dir)

    # now do any processing necessary to process files or just copy

    # copy files that don't need processing add any files that
    # need to be compiled/etc to the ignore lists and then process them
    # after
    EXCLUDE_FILES = ['.gitignore', 'README', 'README.md', '*.exclude.*', '*.exclude', '.gitlab-ci.yml', 'CHANGELOG.md',
                     '.editorconfig']
    EXCLUDE_DIRS = ['.git', '.gitlab']
    if not settings.include_python_source:
        EXCLUDE_DIRS.extend(settings.python_paths)
    copy_module_files(settings.module_dir, build_dir, EXCLUDE_FILES, EXCLUDE_DIRS)

    for script_dir in settings.python_paths:
        # compile python into build_dir
        scriptin = os.path.join(settings.module_dir, script_dir)
        scriptout = os.path.join(build_dir, script_dir)
        compile_script_path = os.path.join(MYDIR, 'compile-python.py')
        ret = os.system('{} -i {} -o {} -c -v'.format(compile_script_path, scriptin, scriptout))
        if (ret != 0):
            sys.stdout.write('Error compiling python scripts')
            remove_build_dir(build_dir)
            sys.exit(1)

        # copy any non python files from the script dir
        EXCLUDE_FILES = EXCLUDE_FILES + ['*.pyc', '*.py']
        copy_module_files(scriptin, scriptout, EXCLUDE_FILES, EXCLUDE_DIRS)

    has_npm_build = get_has_npm_build(build_dir)

    if has_npm_build:
        run_npm_build(build_dir)

    run_pre_package_scripts(settings.pre_package_scripts, build_dir)

    git_hash = get_git_hash(settings.module_dir)
    git_branch = None

    if not settings.version:
        if not settings.git_branch:
            git_branch = get_git_branch(settings.module_dir)
        else:
            git_branch = settings.git_branch
        update_git_info(build_dir, json_file, git_hash, git_branch)
        if settings.buildnum:
            update_build_number(build_dir, json_file, settings.buildnum)
    else:
        update_version(build_dir, json_file, settings.version, git_hash)

    m_json = os.path.join(build_dir, json_file)
    json_data = open(m_json)
    data = json.load(json_data)

    json_data.close()

    if git_hash and git_branch:
        filename = data["name"] + "-" + data["version"] + "-" + git_branch + "-" + git_hash
    elif git_hash:
        filename = data["name"] + "-" + data["version"] + "-" + git_hash
    else:
        filename = data["name"] + "-" + data["version"]

    if settings.dev:
        if not git_branch:
            filename = filename + "-dev"
        update_display_name(build_dir, json_file)

    aptOfflineDir = os.path.join(build_dir, 'apt-offline')
    aptOfflineScript = os.path.abspath(os.path.join(MYDIR, '..', 'ci-tools', 'misc-tools', 'get-apt-offline.sh'))
    if not os.path.exists(aptOfflineDir):
        aptOfflineDir = os.path.join(build_dir, 'support', 'apt-offline')
    if os.path.exists(aptOfflineDir) and not settings.skip_apt_offline_bundles and os.path.exists(aptOfflineScript):
        ret = os.system(aptOfflineScript + ' -d ' + aptOfflineDir)
        if ret != 0:
            print 'Error generating apt-offline bundles'
            remove_build_dir(build_dir)
            sys.exit(1)

    pre_package_cleanup(build_dir)

    filename = filename + ".tgz"
    print "outputting to: " + filename
    make_tarfile(filename, build_dir + os.path.sep)

    remove_build_dir(build_dir)

    if settings.encryptionkey is not None and settings.signingkey is not None:
        print("Encrypting tgz: " + filename + " with " + settings.encryptionkey + ", signing with " +
              settings.signingkey)
        os.system(MYDIR + "/encrypt-data.py -m -t " + filename + " -e " +
                  settings.encryptionkey + " -s " + settings.signingkey)
    else:
        print "Not Encrypting, Need to Specify Encryption and Signing keys (-s and -e)"

    sys.exit(0)


if __name__ == "__main__":
    __main(sys.argv)

__doc__ += __make_parser().format_help()
