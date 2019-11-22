#!/usr/bin/python
"""Utility to Check if all dependencies are met for a set of module tgzs."""
import argparse
import json
import logging
import os
import subprocess
import sys
import tarfile

logger = logging.Logger('check-romg-deps')


def __make_parser():
    p = argparse.ArgumentParser(description='This extracts module.json files from a set of module tgzs '
                                            'and checks dependencies')
    p.add_argument('-b', '--base-path', type=str, help="the path to bits base module tgz", default=None,
                   required=True)
    p.add_argument('-m', '--module-path-list', nargs="+", type=str,
                   help='the list of module tgz locations to be checked for dependencies', default=None, required=True)
    p.add_argument('-v', '--verbose', action='store_true')
    return p


def __check_file_arg(file_name, error_str):
    if not os.path.exists(file_name):
        sys.stderr.write(error_str + ' file not found')
        sys.exit(1)
    try:
        return os.path.abspath(file_name)
    except Exception:
        sys.stderr.write(error_str + ' invalid path')
        sys.exit(1)


def check_module_deps(base_path, module_path_list):
    """Get and check module dependencies for a list of module tgzs.

    Args:
        base_path (str): path to base tgz
        module_path_list (list): list of str paths to module tgz
    Returns:
        True if all dependencies are met false otherwise
    """
    ret = True
    with tarfile.open(base_path, 'r') as base_tgz:
        modules = {}
        base = json.load(base_tgz.extractfile('module.json'))
        for module_path in module_path_list:
            with tarfile.open(module_path, 'r') as module_tgz:
                module_json = json.load(module_tgz.extractfile('module.json'))
                modules[module_json['name']] = module_json
                logger.debug('Found %s\n\t%s\n\n', module_json['name'], module_json)
        if base['version'] == '' or base['version'] is None:
            logger.warn('Skipping all version checks for unversioned base')
        # now do the dependency check
        for module in modules.keys():
            logger.debug('Checking deps for %s', module)
            for dep, version in modules[module]['dependencies'].iteritems():
                if dep == 'bits-base':
                    if not __check_version(version, base['version']):
                        logger.error('Module %s: bits %s does not meet required dependency %s', module, base['version'],
                                     version)
                        ret = False
                elif dep not in modules:
                    logger.error('Module %s does not have required dependency %s', module, dep)
                    ret = False
                elif modules[dep]['version'] != '' and modules[dep]['version'] is not None:
                    logger.debug('Checking version for %s', dep)
                    if not __check_version(version, modules[dep]['version']):
                        logger.error('Module %s: %s %s does not meet required dependency %s', module,
                                     dep, modules[dep]['version'],
                                     version)
                        ret = False
                else:
                    logger.warn('Skipping version check for unversioned %s: %s %s', module, dep, version)
        return ret


def __check_version(version_req, version_str):
    if version_str == '' or version_str is None or version_req == '' or version_req is None:
        return True
    args = ['semver', '-r', version_req, version_str.split('-')[0]]
    logger.debug(args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE)
    p.wait()
    if p.returncode != 0:
        return False
    return True


def __main(argv):
    parser = __make_parser()
    settings = parser.parse_args(argv[1:])
    sh = logging.StreamHandler()
    if settings.verbose:
        sh.setLevel(logging.DEBUG)
    else:
        sh.setLevel(logging.WARN)
    logger.addHandler(sh)
    # get absolute paths and check file inputs for existence
    settings.base_path = __check_file_arg(settings.base_path, 'Invalid argument for base %s' % settings.base_path)
    for index, module_path in enumerate(settings.module_path_list):
        settings.module_path_list[index] = __check_file_arg(module_path,
                                                            'Error invalid module specified %s' % module_path)
    if check_module_deps(settings.base_path, settings.module_path_list):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    __main(sys.argv)
