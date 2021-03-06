#!/usr/bin/python

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
from uuid import uuid4


def __make_parser():
    p = argparse.ArgumentParser(description='This packages up modules and a base into a raw omg file')
    p.add_argument('-n', '--name', type=str, help='the ROMG name', default=None, required=True)
    p.add_argument('-V', '--version', type=str, help='the ROMG version', default=None, required=True)
    p.add_argument('--branch', type=str, help='the ROMG branch', default=None, required=False)
    p.add_argument('-b', '--base', type=str, help='the base packaged for the ROMG', default=None, required=True)
    p.add_argument('-m', '--modules', nargs='+',
                   help='path(s) to module packages that should be included in the omg. multiple modules separated by \
                         spaces', required=True)
    p.add_argument('-o', '--overlays', nargs='*',
                   help='path(s) to overlays that should be overlayed in the omg. multiple overlays separated by \
                         spaces', default=[], required=False)
    p.add_argument('-d', '--output-directory', type=str,
                   help='optional output directory if not given CWD will be used', default='./')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('-a', '--pre-package', action='append', dest='pre_package_scripts', default=[],
                   help='Optional script(s) that will be run just before the romg is packaged that can be used to \
                         minifiy or tweak modules')
    p.add_argument('--build-node-modules', action='store_true',
                   help='if set, "npm run bits:install" will be run on base and all modules')
    p.add_argument('--omg-format-version', type=int, help='set the format version (1 or 2)', default=1)
    p.add_argument('--yarn-offline', action='store_true',
                   help='force the package.json to run yarn with --offline flag (this will edit the file with sed)')
    p.add_argument('-X', '--no-compression', action='store_true',
                   help='disable compression for the tar bundle (romg file) this is useful if you plan on adding \
                         overlays at a later time')
    p.add_argument('-O', '--ownership-info',
                   help='json object with ownership info to set on the OMG at tar time \
                        example: {"uid": 0, "uname": "root", "gid": 1000, "gname": "bits"} ',
                   default=None)
    p.add_argument('--no-dependencies', help="This flag disables dependency checking", action='store_true')
    return p


class romgBuilder(object):
    def __init__(self, logger, tmpDir, name, version, branch=None, omgFormatVersion=1, ownership=None):
        self.tmpDir = tmpDir
        self.logger = logger
        self.info = {'name': name,
                     'version': version,
                     'modules': [],
                     'overlays': {},
                     'arch': 'x86_64'}
        self.gid = None
        self.uid = None
        self.uname = None
        self.gname = None
        if branch is not None:
            self.info['branch'] = branch
        self.dataDir = 'data'
        self.overlayDescriptorDir = None
        if omgFormatVersion == 1:
            self.moduleDir = os.path.join(self.dataDir, 'base', 'modules', 'modules')
            self.baseDir = '.'
            os.makedirs(os.path.abspath(os.path.join(self.tmpDir, self.moduleDir)))
            self.yarnCacheDir = os.path.join(self.tmpDir, 'support', 'yarn-cache')
        else:
            self.moduleDir = 'modules'
            self.baseDir = 'base'
            self.overlayDescriptorDir = os.path.abspath(os.path.join(self.tmpDir, 'overlays'))
            os.makedirs(os.path.abspath(os.path.join(self.tmpDir, self.moduleDir)))
            os.makedirs(os.path.abspath(os.path.join(self.tmpDir, self.dataDir)))
            os.makedirs(os.path.abspath(os.path.join(self.tmpDir, self.baseDir)))
            self.info['uuid'] = str(uuid4())
        if 'OECORE_TARGET_ARCH' in os.environ:
            self.info['arch'] = os.environ['OECORE_TARGET_ARCH']
        if ownership is not None:
            self.uid = ownership['uid']
            self.uname = str(ownership['uname'])
            self.gid = ownership['gid']
            self.gname = str(ownership['gname'])

    def __extractTgz(self, tgzPath, relativeDir='.'):
        extractDir = os.path.abspath(os.path.join(self.tmpDir, relativeDir))
        self.logger.debug('Extracting %s to %s', tgzPath, extractDir)
        with tarfile.open(tgzPath, 'r') as tf:
            tf.extractall(extractDir)
        self._cleanExtractedTgz(extractDir)

    def __extractJsonFromTgz(self, tgzPath, filepath):
        with tarfile.open(tgzPath, 'r') as tf:
            contents = json.loads(tf.extractfile(filepath).read())
        return contents

    def __readModuleJson(self, moduleTgzPath):
        moduleJson = self.__extractJsonFromTgz(moduleTgzPath, 'module.json')
        if 'dependencies' not in moduleJson:
            moduleJson['dependencies'] = {}
        return {'name': moduleJson['name'],
                'version': moduleJson['version'],
                'dependencies': moduleJson['dependencies']}

    def __readOverlayJson(self, overlayTgzPath):
        overlayJson = self.__extractJsonFromTgz(overlayTgzPath, 'overlay.json')
        return {'name': overlayJson['name'], 'version': overlayJson['version']}

    def __get_bits_install(self, moduleDir):
        package_filename = os.path.abspath(os.path.join(moduleDir, "package.json"))
        try:
            with open(package_filename, 'r') as package_file:
                package_info = json.load(package_file)
        except IOError as e:
            self.logger.warning("%s", e)
            return False
        return 'bits:install' in package_info['scripts']

    def __updateYarnCache(self, moduleDir):
        """
        This will update the global omg yarn cache dir (yarn-cache) with the cache dir from the module this is done
        by rsync command to de-duplicate dependencies across all modules, if the module does not have a yarn cache
        dir at support/yarn-cache this step will be skipped.  If it does exist it will be deleted after syncing to the
        global omg yarn-cache dir
        """
        moduleCacheDir = os.path.join(os.path.abspath(os.path.join(moduleDir, 'support', 'yarn-cache')))
        if os.path.isdir(moduleCacheDir):
            p = subprocess.Popen(['rsync', '-a', moduleCacheDir + '/', self.yarnCacheDir + '/'])
            p.wait()
            if p.returncode != 0:
                sys.stderr.write('Failed to sync yarn cache for %s\n' % (moduleDir))
                sys.exit(1)
            shutil.rmtree(moduleCacheDir)

    def __buildModule(self, moduleDir, force_yarn_offline):
        moduleCacheDir = os.path.join(os.path.abspath(os.path.join(moduleDir, 'support', 'yarn-cache')))
        if os.path.isdir(moduleDir) and os.path.isdir(moduleCacheDir):
            environment = os.environ.copy()
            if force_yarn_offline:
                subprocess.call(['sed', '-i', 's/yarn --prod/yarn --prod, --offline/',
                                os.path.join(moduleDir, 'package.json')])
            environment['YARN_CACHE_FOLDER'] = moduleCacheDir
            self.logger.debug('Running "bits:install" for %s', moduleDir)
            cmd = ['npm', 'run', 'bits:install']
            if 'ARCH' in os.environ and os.environ['ARCH'] != 'x86':
                cmd.append('--target_arch=%s' % (os.environ['ARCH']))
            p = subprocess.Popen(cmd, env=environment, cwd=moduleDir)
            p.wait()
            if p.returncode != 0:
                raise Exception('Failed to build yarn for %s\n' % (moduleDir))
            shutil.rmtree(moduleCacheDir)

    def addBase(self, baseTgzPath):
        self.logger.debug("Adding base %s", baseTgzPath)
        baseInfo = self.__readModuleJson(baseTgzPath)
        self.info['base'] = {'name': baseInfo['name'],
                             'version': baseInfo['version']}
        self.info['modules'].append(baseInfo)
        self.__extractTgz(baseTgzPath, self.baseDir)

    def buildBase(self, build_module=False, force_yarn_offline=False):
        self.logger.debug("Building base")
        absBaseDir = os.path.join(self.tmpDir, self.baseDir)
        if build_module:
            if self.__get_bits_install(absBaseDir):
                self.__buildModule(absBaseDir, force_yarn_offline)
            else:
                self.logger.warning("Base doesn't contain a 'bits:install' script")

    def addModule(self, moduleTgzPath):
        self.logger.debug("Adding module %s", moduleTgzPath)
        moduleInfo = self.__readModuleJson(moduleTgzPath)
        self.info['modules'].append(moduleInfo)
        relModuleDir = os.path.join(self.moduleDir, str(moduleInfo['name']))
        self.__extractTgz(moduleTgzPath, relModuleDir)

    def buildModule(self, moduleName, build_module=False, force_yarn_offline=False):
        self.logger.debug("Building module %s", moduleName)
        absModuleDir = os.path.join(self.tmpDir, os.path.join(self.moduleDir, moduleName))
        if build_module:
            if self.__get_bits_install(absModuleDir):
                self.__buildModule(absModuleDir, force_yarn_offline)
            else:
                self.logger.warning("Module %s doesn't contain a 'bits:install' script", moduleName)
        else:
            self.__updateYarnCache(absModuleDir)

    def combineNpmPackages(self, build_module=False, force_yarn_offline=False):
        try:
            p = subprocess.Popen([
                'nip',
                '--ignore', 'node',
                '--ignore', 'node_modules',
                '--ignore', 'bower_components',
                '-d', self.tmpDir,
                '-o', self.tmpDir,
                '-u'
            ], env=os.environ.copy())
            p.wait()
        except OSError as e:
            if e.errno == os.errno.ENOENT:
                self.logger.warning('nip command not found, \
                @lgslabs/nip should be installed for a better packaging experience.')
            else:
                raise e
        self.logger.debug("Building shared package.json at %s", self.tmpDir)
        try:
            os.makedirs(os.path.abspath(os.path.join(self.tmpDir, 'support', 'yarn-cache')))
        except OSError as e:
            if e.errno != os.errno.EEXIST:
                raise e
        if build_module:
            if self.__get_bits_install(self.tmpDir):
                self.__buildModule(self.tmpDir, force_yarn_offline)
            else:
                self.logger.warning("root level package.json doesn't contain a 'bits:install' script")
        else:
            self.__updateYarnCache(self.tmpDir)

    def addOverlay(self, overlayTgzPath):
        self.logger.debug("Adding overlay %s", overlayTgzPath)
        overlayInfo = self.__readOverlayJson(overlayTgzPath)
        self.info['overlays'][overlayInfo['name']] = {'version': overlayInfo['version']}
        self.__extractTgz(overlayTgzPath)
        overlayJson = os.path.abspath(os.path.join(self.tmpDir, 'overlay.json'))
        if os.path.isfile(overlayJson) and self.overlayDescriptorDir is not None:
            if not os.path.isdir(self.overlayDescriptorDir):
                os.makedirs(self.overlayDescriptorDir)
            os.rename(overlayJson,
                      os.path.join(self.overlayDescriptorDir,
                                   overlayInfo['name'] + '_' + overlayInfo['version'] + '.json'))

    def writeRomg(self, outputDir, disableCompression=False):
        if 'branch' in self.info:
            sRomgFilename = '%s_%s_%s.romg' % (self.info['name'],
                                               self.info['branch'],
                                               self.info['version'])
            sRomgInfoFilename = '%s_%s_%s_header.json' % (self.info['name'],
                                                          self.info['branch'],
                                                          self.info['version'])
        else:
            sRomgFilename = '%s_%s.romg' % (self.info['name'], self.info['version'])
            sRomgInfoFilename = '%s_%s_header.json' % (self.info['name'], self.info['version'])
        sRomgFilepath = os.path.join(outputDir, sRomgFilename)
        sRomgInfoFilepath = os.path.join(outputDir, sRomgInfoFilename)
        self.logger.debug('Outputing to %s %s', sRomgFilepath, sRomgInfoFilepath)
        tarFlags = "w"
        if not disableCompression:
            tarFlags += ":gz"
        with tarfile.open(sRomgFilepath, tarFlags) as tar:
            tar.add(self.tmpDir, arcname='./', filter=self.__tar_chown)
        with open(sRomgInfoFilepath, 'w') as infoFile:
            infoFile.write(json.dumps(self.info, indent=2, separators=(',', ': ')))

    def __tar_chown(self, tarinfo):
        if self.gid is not None and self.uid is not None:
            tarinfo.uid = self.uid
            tarinfo.gid = self.gid
            tarinfo.uname = self.uname
            tarinfo.gname = self.gname
            # no permissions for other
            tarinfo.mode &= ~0x07
        return tarinfo

    def runPrepackageScripts(self):
        scriptDir = os.path.abspath(os.path.join(self.tmpDir, 'prepackage_scripts'))
        if os.path.isdir(scriptDir):
            scripts = [f for f in os.listdir(scriptDir) if os.path.isfile(os.path.join(scriptDir, f))]
            for scriptName in scripts:
                scriptPath = os.path.join(scriptDir, scriptName)
                self.logger.info("Running %s", scriptPath)
                ret = subprocess.call([scriptPath], cwd=self.tmpDir)
                if ret != 0:
                    self.logger.error('Failed to run %s', ret)
                    raise Exception('Failed to run prepackage hook %s', scriptPath)
            shutil.rmtree(scriptDir)

    def _cleanExtractedTgz(self, dir):
        """
        This cleans up any files we want to ensure do not make it into the final
        """
        gitlabDir = os.path.join(dir, ".gitlab")
        self.logger.debug("Checking for gitlab dir %s", gitlabDir)
        if os.path.exists(gitlabDir):
            self.logger.debug("Removing gitlab dir %s", gitlabDir)
            shutil.rmtree(gitlabDir)


def checkFileArg(fileName, errorStr):
    if not os.path.exists(fileName):
        sys.stderr.write(errorStr + ' file not found')
        sys.exit(1)
    try:
        return os.path.abspath(fileName)
    except Exception:
        sys.stderr.write(errorStr + ' invlid path')
        sys.exit(1)


def run_pre_package_scripts(scripts, buildDir):
    print 'Scripts: ', scripts
    my_env = os.environ.copy()

    for script in scripts:
        print "Running " + script
        try:
            args = script.split(' ')
            ret = subprocess.call(args, cwd=buildDir, env=my_env)
            if ret != 0:
                print 'Failed to run ', ret
        except Exception as e:
            print 'Failed to run script ', e


def __main(argv):
    parser = __make_parser()
    settings = parser.parse_args(argv[1:])
    logger = logging.Logger('package-romg')
    sh = logging.StreamHandler()
    if settings.verbose:
        sh.setLevel(logging.DEBUG)
    else:
        sh.setLevel(logging.ERROR)
    logger.addHandler(sh)
    # get absolute paths and check file inputs for existence
    settings.base = checkFileArg(settings.base, 'Invalid argument for base %s' % (settings.base))
    settings.modules = [checkFileArg(modulePath, 'Error invalid module specified %s' %
                                     modulePath) for modulePath in settings.modules]
    settings.overlays = [checkFileArg(overlayPath, 'Error invalid overlay specified %s' %
                                      (overlayPath)) for overlayPath in settings.overlays]
    settings.output_directory = checkFileArg(settings.output_directory, 'Error invalid output dir')
    if settings.ownership_info:
        try:
            settings.ownership_info = json.loads(settings.ownership_info)
        except ValueError:
            logger.error('Could not parse ownership info: %s\n\n', settings.ownership_info)
            parser.print_help()
            sys.exit(1)
    logger.debug("Base: %s Modules: %s Overlays: %s", settings.base, settings.modules, settings.overlays)
    tmpDir = tempfile.mkdtemp(prefix='romg-')
    logger.debug('Using temp dir %s', tmpDir)
    if settings.no_dependencies:
        logger.debug("Skipping dependency check...")
    else:
        logger.debug("Checking module dependencies...")
        env = os.environ
        args = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'integratedcheckromgdeps.py'),
            '-b', settings.base,
            '-m'
        ]
        for module in settings.modules:
            args.append(module)
        dependency_check_process = subprocess.Popen(args, env=env)
        dependency_check_process.wait()
        if dependency_check_process.returncode != 0:
            raise Exception("ERROR: Module dependencies not properly met. "
                            "If this is intentional rerun with --no-dependencies.")
    romg = romgBuilder(logger, tmpDir, settings.name, settings.version, settings.branch, settings.omg_format_version,
                       settings.ownership_info)
    romg.addBase(settings.base)
    for module in settings.modules:
        romg.addModule(module)
    romg.combineNpmPackages(settings.build_node_modules, settings.yarn_offline)
    romg.buildBase(settings.build_node_modules, settings.yarn_offline)
    for module in romg.info['modules']:
        if(str(module['name']) != 'bits-base'):
            romg.buildModule(str(module['name']), settings.build_node_modules, settings.yarn_offline)
    for overlay in settings.overlays:
        romg.addOverlay(overlay)
    # run pre-package scripts specified by the command line
    run_pre_package_scripts(settings.pre_package_scripts, tmpDir)
    # run any pre-package scripts in overlays
    romg.runPrepackageScripts()
    romg.writeRomg(settings.output_directory, settings.no_compression)

    # clean up temp dir
    shutil.rmtree(tmpDir)
    sys.exit(0)


if __name__ == "__main__":
    __main(sys.argv)
