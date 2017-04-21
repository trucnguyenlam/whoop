#!/usr/bin/env python2.7

from __future__ import print_function
import getopt
import os
import signal
import subprocess
import sys
import threading
import timeit
import pprint
import fnmatch
import shutil
import re

VERSION = '0.7'

try:
  import psutil
  psutilPresent = True
except ImportError:
  psutilPresent = False

""" This class uses exceptions to exit the tool and report
success or error e.g. related to IO.
"""
class ReportAndExit(Exception):
  def __init__(self, code, msg=None):
    self.code = code
    self.msg = msg

  def getExitCode(self):
    return self.code

  def __str__(self):
    codeString = None
    for cs in [ x for x in dir(ErrorCodes) if not x.startswith('_') ]:
      if getattr(ErrorCodes, cs) == self.code:
        codeString = cs
    if codeString == None: codeString = 'UNKNOWN'
    retStr = 'Whoop: {} error ({})'.format(codeString, self.code)
    if self.msg: retStr = retStr + ': ' + self.msg
    return retStr

class Timeout(Exception):
    pass

""" This class implements the error codes related
to the Whoop toolchain.
"""
class ErrorCodes(object):
  SUCCESS = 0
  COMMAND_LINE_ERROR = 1
  CONFIGURATION_ERROR = 2
  CLANG_ERROR = 3
  SMACK_ERROR = 4
  WHOOP_ERROR = 5
  DRIVER_ERROR = 6
  CORRAL_ERROR = 7
  TIMEOUT = 8
  CTRL_C = 9

# Try to import the paths need for the Whoop toolchain
try:
  import findtools
  findtools.init(sys.path[0])
except ImportError:
  raise ReportAndExit(ErrorCodes.CONFIGURATION_ERROR,
                      'Cannot find \'findtools.py\'.'
                      ' Did you forget to create it from a template?')

""" This class allows functions to be registered and later
called using the call() method.
"""
class BatchCaller(object):
  def __init__(self, verbose=False):
    from collections import namedtuple
    self.calls = [ ]
    self.verbose = verbose
    self.fcallType = namedtuple('FCall',['function', 'nargs', 'kargs'])

  def setVerbose(self, v=True):
    self.verbose = v

  def register(self, function, *nargs, **kargs):
    call = self.fcallType(function, nargs, kargs)
    self.calls.append(call)

  def call(self, inReverse=False):
    if inReverse: self.calls.reverse()
    for call in self.calls:
      if self.verbose:
        print("Clean up handler Calling " + str(call.function.__name__) + '(' + \
              str(call.nargs) + ', ' + str(call.kargs) + ')')
      call.function(*(call.nargs), **(call.kargs))

  def clear(self):
    self.calls = [ ]
    assert len(self.calls) == 0

cleanUpHandler = BatchCaller()

""" Timing for the toolchain.
"""
Tools = [ "chauffeur", "clang", "smack", "whoopEngine", "whoopCruncher", "whoopRaceChecker", "corral" ]
Timing = { }

""" WindowsError is not defined on UNIX
systems, this works around that.
"""
try:
  WindowsError
except NameError:
  class WindowsError(Exception):
    pass

""" We only support C at the moment.
"""
class SourceLanguage(object):
  Unknown = 0
  C = 1

clangCoreIncludes = [
                      findtools.smackSrcDir + "/smack",
                      findtools.whoopDir + "/Model",
                      findtools.whoopDir + "/Model/linux",
                      findtools.whoopDir + "/Model/asm"
                    ]
clangOtherIncludes = [
                      findtools.smackSrcDir + "/smack",
                      findtools.whoopDir + "/OtherModels/seq1/include/ddverify",
                      findtools.whoopDir + "/OtherModels/seq1/include",
                      findtools.whoopDir + "/OtherModels/seq1/include/linux"
                    ]
clangCoreDefines = [ ]

""" This class defines all the default options for
the Drify toolchain.
"""
class DefaultCmdLineOptions(object):
  def __init__(self):
    self.SL = SourceLanguage.Unknown
    self.sourceFiles = [ ]
    self.chauffeurOptions = [ ]
    self.clangOptions = [ "-w", "-g", "-emit-llvm", "-O0", "-c", "-DMEMORY_MODEL_NO_REUSE_IMPLS" ]
    self.smackOptions = [ ]
    self.whoopEngineOptions = [ ]
    self.whoopCruncherOptions = [ ]
    self.whoopRaceCheckerOptions = [ "/nologo", "/typeEncoding:m", "/mv:-", "/doNotUseLabels", "/enhancedErrorMessages:1" ]
    self.corralOptions = [ ]
    self.includes = []
    self.defines = clangCoreDefines
    self.analyseOnly = ""
    self.onlyRaces = False
    self.onlyDeadlocks = False
    self.findBugs = False
    self.skipNonRacyPairs = False
    self.noInfer = False
    self.inline = False
    self.inlineBound = 0
    self.k = 2
    self.recursionBound = 1
    self.staticLoopBound = 0
    self.yieldNoAccess = False
    self.yieldAll = False
    self.yieldCoarse = False
    self.yieldRaceChecking = False
    self.optimizeCorral = False
    self.showCorralStats = False
    self.noHeavyAsyncCallsOptimisation = False
    self.checkInParamAliasing = False
    self.noExistentialOpts = False
    self.useOtherModel = False
    self.verbose = False
    self.silent = False
    self.printPairs = False
    self.generateSmt2 = False
    self.keepTemps = False
    self.debugging = False
    self.time = False
    self.timeCSVLabel = None
    self.timePasses = None
    self.componentTimeout = 0
    self.solver = "z3"
    self.logic = "AUFLIRA"
    self.stopAtRe = False
    self.stopAtBc = False
    self.stopAtBpl = False
    self.stopAtEngine = False
    self.stopAtCruncher = False
    self.stopAtRaceChecker = False
    self.skip = { "chauffeur": False,
                  "clang": False,
                  "smack": False,
                  "engine": False,
                  "cruncher": False,
                  "raceChecker": False }

CommandLineOptions = DefaultCmdLineOptions()

def showVersionAndExit():
  print("Whoop " + VERSION)
  raise ReportAndExit(ErrorCodes.SUCCESS)

def showHelpAndExit():
  stringReplacements = {
    'componentTimeout': CommandLineOptions.componentTimeout,
    'solver': CommandLineOptions.solver,
    'logic': CommandLineOptions.logic
  }

  print("""OVERVIEW: Whoop - a data race analyser for Linux device drivers

  This is an experimental tool from the Multicore Programming Group at Imperial College London.

  USAGE: whoop.py [options] <inputs>

  GENERAL OPTIONS:
    -h, --help              Display this message.
    -I <value>              Add directory to include search path.
    -D <value>              Define symbol.
    --find-bugs             Runs Corral after race checking the program to find bugs.
    --timeout=X             Allow each tool in the toolchain to run for X seconds before giving up.
                            A timeout of 0 disables the timeout. The default is {componentTimeout} seconds.
    --verbose               Show commands to run and use verbose output.
    --time                  Show timing information.
    -V, --version           Show version information.

  ADVANCED OPTIONS:
    --print-pairs           Print information about the entry point pairs.
    --inline                Inline all device driver non-entry point functions during Clang's AST traversal.
    --inline-bound=X        Inline all device driver non-entry point functions during the Whoop instrumentation,
                            for entry points with less or equal than X nested function calls.
    --k=X                   Use Corral's /k.
    --recursion-bound=X     Use Corral's /recursionBound.
    --static-loop-bound=X   Use Corral's /maxStaticLoopBound.
    --inparam-aliasing      Disable assumption that inparams cannot alias.
    --no-existential-opts   Do not perform existential optimisations.
    --analyse-only=X        Specify entry point to be analysed. All others are skipped.
    --no-infer              Turn off invariant inference.
    --skip-non-racy-pairs   Skip race free pairs from Corral analysis.
    --yield-all             Instruments yields in all visible operations.
    --yield-coarse          Instruments yields in a coarse granularity manner.
    --yield-no-access       Turn off yield instrumentation in memory accesses.
    --yield-race-check      Instruments race checking in yielded memory accesses.
    --time-passes           Show timing information for the various analysis and instrumentation passes.
    --other-model           Uses an alternative environmental model.

  SOLVER OPTIONS:
    --gen-smt2              Generate smt2 file.
    --solver=X              Choose which SMT Theorem Prover to use in the backend.
                            Available options: 'Z3' or 'cvc4' (default is '{solver}').
    --logic=X               Define the logic to be used by the CVC4 SMT solver backend
                            (default is {logic}).
  TOOL OPTIONS:
    --clang-opt=...         Specify option to be passed to Clang.
    --smack-opt=...         Specify option to be passed to SMACK.
    --whoop-opt=...         Specify option to be passed to Whoop.
    --whoop-file=X.bpl      Specify a supporting .bpl file to be used during verification.
    --debug                 Enable debugging of verify components: exceptions will
                            not be suppressed.
    --keep-temps            Keep intermediate bc and bpl.
    --stop-at-re            Stop after generating the refactored driver source code.
    --stop-at-bc            Stop after generating bc.
    --stop-at-bpl           Stop after generating bpl.
    --stop-at-engine        Stop after the Whoop instrumentation engine.
    --stop-at-cruncher      Stop after the Whoop invariant inference cruncher.
    --skip-until-clang      Start toolchain at the CLANG compiler frontend.
    --skip-until-model      Start toolchain at the SMACK LLVM-IR to Boogie translation tool.
    --skip-until-engine     Start toolchain at the Whoop instrumentation engine.
    --skip-until-cruncher   Start toolchain at the Whoop invariant inference cruncher.
    --skip-until-checker    Start toolchain at the Whoop race checker.
    --skip-until-corral     Start toolchain at the Corral bug finder.
    --time-as-csv=label     Print timing as CSV row with label.
    --silent                Silent on success; only show errors/timing.
  """.format(**stringReplacements))
  raise ReportAndExit(ErrorCodes.SUCCESS)

def showWarning(msg):
  print("Whoop: warning: " + msg)

def verbose(msg):
  if(CommandLineOptions.verbose):
    print(msg)

def splitFilenameExt(f):
  filename, ext = os.path.splitext(f)
  if filename.endswith(".re") and ext == ".c":
    filename, unused_ext_ = os.path.splitext(filename)
    ext = ".re.c"
  return filename, ext

def getSourceFiles(args):
  if len(args) == 0:
    raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "no .c file(s) supplied")
  for a in args:
    filename, ext = splitFilenameExt(a)
    if ext == ".c":
      CommandLineOptions.SL = SourceLanguage.C
    elif ext in [ "re.c", ".bc", ".bpl" ]:
      CommandLineOptions.skip["chauffeur"] = True
      if ext in [ ".bc", ".bpl" ]:
        CommandLineOptions.skip["clang"] = True
      if ext in [ ".bpl" ]:
        CommandLineOptions.skip["smack"] = True
    else:
      raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "'" + a + "' has unknown file extension, the supported file extension is .c")
    CommandLineOptions.sourceFiles.append(a)

def showHelpIfRequested(opts):
  for o, a in opts:
    if o == "--help" or o == "-h":
      showHelpAndExit()

def showVersionIfRequested(opts):
  for o, a in opts:
    if o == "--version" or o == "-V":
      showVersionAndExit()

def processGeneralOptions(opts, args):
  # All options that can be processed without resulting in an error go
  # in this loop. Some of these we want to handle even when some other
  # option results in an error, e.g., the time related options.
  for o, a in opts:
    if o == "-D":
      CommandLineOptions.defines.append(a)
    if o == "-I":
      CommandLineOptions.includes.append(a)
    if o == "--verbose":
      CommandLineOptions.verbose = True
    if o == "--silent":
      CommandLineOptions.silent = True
    if o == "--print-pairs":
      CommandLineOptions.printPairs = True
    if o == "--debug":
      CommandLineOptions.debugging = True
    if o == "--analyse-only":
      CommandLineOptions.analyseOnly += str(a)
    if o == "--only-race-checking":
      CommandLineOptions.onlyRaces = True
    if o == "--only-deadlock-checking":
      CommandLineOptions.onlyDeadlocks = True
    if o == "--find-bugs":
      CommandLineOptions.findBugs = True
    if o == "--skip-non-racy-pairs":
      CommandLineOptions.skipNonRacyPairs = True
    if o == "--no-infer":
      CommandLineOptions.noInfer = True
    if o == "--yield-all":
      CommandLineOptions.yieldAll = True
    if o == "--yield-coarse":
      CommandLineOptions.yieldCoarse = True
    if o == "--yield-no-access":
      CommandLineOptions.yieldNoAccess = True
    if o == "--yield-race-check":
      CommandLineOptions.yieldRaceChecking = True
    if o == "--optimize-corral":
      CommandLineOptions.optimizeCorral = True
    if o == "--show-corral-stats":
      CommandLineOptions.showCorralStats = True
    if o == "--no-heavy-async-calls-optimisation":
      CommandLineOptions.noHeavyAsyncCallsOptimisation = True
    if o == "--inparam-aliasing":
      CommandLineOptions.checkInParamAliasing = True
    if o == "--no-existential-opts":
      CommandLineOptions.noExistentialOpts = True
    if o == "--other-model":
      CommandLineOptions.useOtherModel = True
    if o == "--keep-temps":
      CommandLineOptions.keepTemps = True
    if o == "--inline":
      CommandLineOptions.inline = True
    if o == "--time":
      CommandLineOptions.time = True
    if o == "--time-as-csv":
      CommandLineOptions.time = True
      CommandLineOptions.timeCSVLabel = a
    if o == "--time-passes":
      CommandLineOptions.timePasses = True
    if o == "--clang-opt":
      CommandLineOptions.clangOptions += str(a).split(" ")
    if o == "--smack-opt":
      CommandLineOptions.smackOptions += str(a).split(" ")
    if o == "--boogie-opt":
      CommandLineOptions.whoopEngineOptions += str(a).split(" ")
      CommandLineOptions.whoopCruncherOptions += str(a).split(" ")
      CommandLineOptions.whoopRaceCheckerOptions += str(a).split(" ")
    if o == "--gen-smt2":
      CommandLineOptions.generateSmt2 = True
    if o == "--stop-at-re":
      CommandLineOptions.stopAtRe = True
    if o == "--stop-at-bc":
      CommandLineOptions.stopAtBc = True
    if o == "--stop-at-bpl":
      CommandLineOptions.stopAtBpl = True
    if o == "--stop-at-engine":
      CommandLineOptions.stopAtEngine = True
    if o == "--stop-at-cruncher":
      CommandLineOptions.stopAtCruncher = True
    if o == "--stop-at-race-checker":
      CommandLineOptions.stopAtRaceChecker = True
    if o == "--skip-until-clang":
      CommandLineOptions.skip["chauffeur"] = True
    if o == "--skip-until-model":
      CommandLineOptions.skip["chauffeur"] = True
      CommandLineOptions.skip["clang"] = True
    if o == "--skip-until-engine":
      CommandLineOptions.skip["chauffeur"] = True
      CommandLineOptions.skip["clang"] = True
      CommandLineOptions.skip["smack"] = True
    if o == "--skip-until-cruncher":
      CommandLineOptions.skip["chauffeur"] = True
      CommandLineOptions.skip["clang"] = True
      CommandLineOptions.skip["smack"] = True
      CommandLineOptions.skip["engine"] = True
    if o == "--skip-until-checker":
      CommandLineOptions.skip["chauffeur"] = True
      CommandLineOptions.skip["clang"] = True
      CommandLineOptions.skip["smack"] = True
      CommandLineOptions.skip["engine"] = True
      CommandLineOptions.skip["cruncher"] = True
    if o == "--skip-until-corral":
      CommandLineOptions.skip["chauffeur"] = True
      CommandLineOptions.skip["clang"] = True
      CommandLineOptions.skip["smack"] = True
      CommandLineOptions.skip["engine"] = True
      CommandLineOptions.skip["cruncher"] = True
      CommandLineOptions.skip["raceChecker"] = True

  # All options whose processing can result in an error go in this loop.
  # See also the comment above the previous loop.
  for o, a in opts:
    if o == "--timeout":
      try:
        CommandLineOptions.componentTimeout = int(a)
        if CommandLineOptions.componentTimeout < 0:
          raise ValueError
      except ValueError as e:
          raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "Invalid timeout \"" + a + "\"")
    if o == "--boogie-file":
      filename, ext = splitFilenameExt(a)
      if ext != ".bpl":
        raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "'" + a + "' specified via --boogie-file should have extension .bpl")
      CommandLineOptions.whoopEngineOptions += [ a ]
      CommandLineOptions.whoopCruncherOptions += [ a ]
      CommandLineOptions.whoopRaceCheckerOptions += [ a ]
    if o == "--solver":
      if a.lower() in ("z3","cvc4"):
        CommandLineOptions.solver = a.lower()
      else:
        raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "argument to --solver must be 'Z3' or 'CVC4'")
    if o == "--logic":
      if a.upper() in ("ALL_SUPPORTED","QF_ALL_SUPPORTED","AUFLIRA"):
        CommandLineOptions.logic = a.upper()
      else:
        raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "argument to --logic must be 'ALL_SUPPORTED', 'QF_ALL_SUPPORTED' or 'AUFLIRA'")
    if o == "--inline-bound":
      try:
        CommandLineOptions.inlineBound = int(a)
        if CommandLineOptions.inlineBound < 0:
          raise ValueError
      except ValueError as e:
          raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "Invalid inlining bound \"" + a + "\"")
    if o == "--k":
      try:
        CommandLineOptions.k = int(a)
        if CommandLineOptions.k < 0:
          raise ValueError
      except ValueError as e:
          raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "Invalid k \"" + a + "\"")
    if o == "--recursion-bound":
      try:
        CommandLineOptions.recursionBound = int(a)
        if CommandLineOptions.recursionBound < 0:
          raise ValueError
      except ValueError as e:
          raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "Invalid recursion bound \"" + a + "\"")
    if o == "--static-loop-bound":
      try:
        CommandLineOptions.staticLoopBound = int(a)
        if CommandLineOptions.staticLoopBound < 0:
          raise ValueError
      except ValueError as e:
          raise ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, "Invalid static loop bound \"" + a + "\"")

""" This class is used by run() to implement a timeout for tools. It
uses threading.Timer to implement the timeout and provides a method
for checking if the timeout occurred. It also provides a method for
cancelling the timeout. The class is reentrant.
"""
class ToolWatcher(object):
  def __handleTimeOut(self):
    if self.popenObject.poll() == None :
      # Program is still running, let's kill it
      self.__killed=True
      if psutilPresent:
        children = psutil.Process(self.popenObject.pid).get_children(True)
      self.popenObject.terminate()
      if psutilPresent:
        for child in children:
          child.terminate()

  def __init__(self,popenObject,timeout):
    self.timeout = timeout
    self.popenObject = popenObject
    self.__killed = False
    self.timer = threading.Timer(self.timeout, self.__handleTimeOut)
    self.timer.start()

  def timeOutOccured(self):
    return self.__killed

  def cancelTimeout(self):
    self.timer.cancel()

""" Run a command with an optional timeout. A timeout
of zero implies no timeout.
"""
def run(command, timeout=0):
  popenargs = { }
  if CommandLineOptions.verbose:
    print(" ".join(command))
  else:
    popenargs['bufsize'] = 0
    if __name__ != '__main__':
      popenargs['stdout'] = subprocess.PIPE
  if CommandLineOptions.silent:
    popenargs['stdout'] = subprocess.PIPE
  popenargs['stderr'] = subprocess.STDOUT
  popenargs['stdin'] = subprocess.PIPE

  killer = None
  def cleanupKiller():
    if killer != None:
      killer.cancelTimeout()

  proc = subprocess.Popen(command, **popenargs)
  if timeout > 0:
    killer = ToolWatcher(proc,timeout)
  try:
    stdout, stderr = proc.communicate()
    if killer != None and killer.timeOutOccured():
      raise Timeout
  except KeyboardInterrupt:
    cleanupKiller()
    proc.wait()
    raise ReportAndExit(ErrorCodes.CTRL_C)
  finally:
    cleanupKiller()

  return stdout, proc.returncode

""" Run a tool. If the timeout is set to 0 then there will be no
timeout.
"""
def runTool(ToolName, Command, ErrorCode, timeout=0):
  assert ToolName in Tools
  verbose("Running " + ToolName)
  remainingTime = timeout
  try:
    start = timeit.default_timer()
    if timeout > 0 and Timing.has_key(ToolName):
      remainingTime = timeout - int(Timing[ToolName])
      if remainingTime < 1:
        remainingTime = 1
    stdout, returnCode = run(Command, remainingTime)
    end = timeit.default_timer()
  except Timeout:
    if CommandLineOptions.time:
      if Timing.has_key(ToolName):
        Timing[ToolName] = Timing[ToolName] + remainingTime
      else:
        Timing[ToolName] = timeout
    raise ReportAndExit(ErrorCodes.TIMEOUT, ToolName + " timed out. " + \
                        "Use --timeout=N with N > " + str(timeout)    + \
                        " to increase timeout, or --timeout=0 to "    + \
                        "disable timeout.")
  except (OSError, WindowsError) as e:
    raise ReportAndExit(ErrorCode, "While invoking " + ToolName       + \
                        ": " + str(e) + "\nWith command line args:\n" + \
                        pprint.pformat(Command))
  if CommandLineOptions.time:
    if Timing.has_key(ToolName):
      Timing[ToolName] = Timing[ToolName] + end-start
    else:
      Timing[ToolName] = end-start
  if returnCode != ErrorCodes.SUCCESS:
    if not (CommandLineOptions.findBugs and ToolName == "whoopRaceChecker"):
      if CommandLineOptions.silent and stdout: print(stdout, file=sys.stderr)
      raise ReportAndExit(ErrorCode, stdout)

def runCorral(filename):
    directory = os.path.dirname(os.path.realpath(filename))
    inputFile = os.path.splitext(os.path.basename(filename))[0]
    counter = 0
    for file in os.listdir(directory):
      if fnmatch.fnmatch(file, inputFile + '_check_racy_*.bpl'):
        runTool("corral",
                (["mono"] if os.name == "posix" else []) +
                [findtools.corralBinDir + "/corral.exe"] +
                CommandLineOptions.corralOptions + [ directory + os.sep + file ],
                ErrorCodes.CORRAL_ERROR,
                CommandLineOptions.componentTimeout)
        counter += 1
        if CommandLineOptions.showCorralStats:
          print("Pairs analysed so far: " + str(counter))
          print("Time elapsed so far: " + str(Timing["corral"]))

def addInline(match, info):
  foundit = False
  procName = match.group(1)
  procDef = ''

  with open (info, "r") as info:
    for line in info.readlines():
      if "::" in line and line.split('::')[1].replace('\n', '') == procName:
        foundit = True
        break

  if foundit:
    procDef += 'procedure ' + procName + '('
  else:
    procDef += 'procedure {:inline 1} ' + procName + '('
  return procDef

def processBPL(file, info):
  p = re.compile('procedure[ ]*([a-zA-Z0-9_$]*)[ ]*\(')
  with open(file, "r+") as f:
    bpl = p.sub(lambda match: addInline(match, info), f.read())
    f.seek(0)
    f.write(bpl)

""" This function should NOT be called directly instead call
main(). It is assumed that argv has had sys.argv[0] removed.
"""
def startToolChain(argv):
  progname = __name__
  if progname.endswith('.py'): progname = progname[:-3]

  try:
    opts, args = getopt.gnu_getopt(argv,'hVD:I:',
             ['help', 'version', 'debug', 'verbose', 'silent',
              'find-bugs', 'only-race-checking', 'only-deadlock-checking',
              'time', 'time-as-csv=', 'time-passes',
              'keep-temps', 'print-pairs',
              'clang-opt=', 'smack-opt=',
              'boogie-opt=', 'timeout=', 'boogie-file=',
              'analyse-only=', 'inline', 'inline-bound=', 'k=', 'recursion-bound=', 'static-loop-bound=',
              'no-infer', 'no-heavy-async-calls-optimisation', 'skip-non-racy-pairs',
              'yield-all', 'yield-coarse', 'yield-no-access', 'yield-race-check',
              'optimize-corral', 'show-corral-stats',
              'inparam-aliasing', 'no-existential-opts',
              'gen-smt2', 'solver=', 'logic=', 'other-model',
              'stop-at-re', 'stop-at-bc', 'stop-at-bpl', 'stop-at-engine',
              'stop-at-cruncher', 'stop-at-race-checker',
              'skip-until-clang', 'skip-until-model', 'skip-until-engine',
              'skip-until-cruncher', 'skip-until-checker', 'skip-until-corral'
             ])
  except getopt.GetoptError as getoptError:
    ReportAndExit(ErrorCodes.COMMAND_LINE_ERROR, getoptError.msg + ".  Try --help for list of options")

  showHelpIfRequested(opts)
  showVersionIfRequested(opts)

  getSourceFiles(args)
  processGeneralOptions(opts, args)

  filename, ext = splitFilenameExt(args[0])

  # Intermediate filenames
  reFilename = filename + '.re.c'
  bcFilename = filename + '.bc'
  bplFilename = filename + '.bpl'
  wbplFilename = filename + '.wbpl'
  infoFilename = filename + '.info'
  fpFilename = filename + '.fp.info'
  summaryInfoFilename = filename + '.summaries.info'
  smt2Filename = filename + '.smt2'
  if not CommandLineOptions.keepTemps:
    inputFilename = filename + ext
    def DeleteFile(filename):
      """ Delete the filename if it exists; but don't delete the original input """
      if filename == inputFilename: return
      try: os.remove(filename)
      except OSError: pass
    def DeleteFilesWithPattern(pattern):
      """ Delete all the files with the given pattern if they exist """
      thisfile = os.path.splitext(os.path.basename(inputFilename))[0]
      path = os.path.realpath(inputFilename).replace(os.path.basename(inputFilename), "")
      for file in os.listdir(os.path.dirname(os.path.realpath(inputFilename))):
        if fnmatch.fnmatch(file, thisfile + '*.' + pattern):
          try: os.remove(path + file)
          except OSError: pass
    cleanUpHandler.register(DeleteFile, bcFilename)
    if not CommandLineOptions.stopAtRe: cleanUpHandler.register(DeleteFile, reFilename)
    if not CommandLineOptions.stopAtRe: cleanUpHandler.register(DeleteFile, infoFilename)
    if not CommandLineOptions.stopAtRe: cleanUpHandler.register(DeleteFile, fpFilename)
    if not CommandLineOptions.stopAtBpl: cleanUpHandler.register(DeleteFile, bplFilename)
    if not CommandLineOptions.stopAtEngine: cleanUpHandler.register(DeleteFilesWithPattern, wbplFilename)
    if not CommandLineOptions.stopAtEngine: cleanUpHandler.register(DeleteFile, summaryInfoFilename)
    if not CommandLineOptions.stopAtCruncher: cleanUpHandler.register(DeleteFilesWithPattern, "wbpl")
    if not CommandLineOptions.stopAtRaceChecker: cleanUpHandler.register(DeleteFilesWithPattern, "bpl")

  if CommandLineOptions.useOtherModel:
    global clangCoreIncludes
    clangCoreIncludes = clangOtherIncludes
  CommandLineOptions.includes += clangCoreIncludes

  if CommandLineOptions.inline:
    CommandLineOptions.chauffeurOptions.append("-inline")
    CommandLineOptions.whoopEngineOptions += [ "/inline" ]
    CommandLineOptions.whoopCruncherOptions += [ "/inline" ]
  CommandLineOptions.chauffeurOptions.append(filename + ext)
  CommandLineOptions.chauffeurOptions.append("--")
  CommandLineOptions.chauffeurOptions.append("-w")

  CommandLineOptions.clangOptions.append("-o")
  CommandLineOptions.clangOptions.append(bcFilename)
  CommandLineOptions.clangOptions.append(reFilename)

  if ext in [ ".c" ]:
    CommandLineOptions.smackOptions += [ bcFilename, "-o", bplFilename ]
    CommandLineOptions.smackOptions += [ "--source-loc-syms" ]

  CommandLineOptions.whoopEngineOptions += [ "/whoopDecl:" + findtools.whoopDir + os.sep + "Model" + os.sep + "whoop_decl.bpl" ]
  CommandLineOptions.whoopCruncherOptions += [ "/whoopDecl:" + findtools.whoopDir + os.sep + "Model" + os.sep + "whoop_decl.bpl" ]
  CommandLineOptions.whoopRaceCheckerOptions += [ "/whoopDecl:" + findtools.whoopDir + os.sep + "Model" + os.sep + "whoop_decl.bpl" ]

  if CommandLineOptions.solver == "cvc4":
    CommandLineOptions.whoopEngineOptions += [ "/proverOpt:SOLVER=cvc4" ]
    CommandLineOptions.whoopCruncherOptions += [ "/proverOpt:SOLVER=cvc4" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/proverOpt:SOLVER=cvc4" ]
    CommandLineOptions.whoopEngineOptions += [ "/cvc4exe:" + findtools.cvc4BinDir + os.sep + "cvc4.exe" ]
    CommandLineOptions.whoopCruncherOptions += [ "/cvc4exe:" + findtools.cvc4BinDir + os.sep + "cvc4.exe" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/cvc4exe:" + findtools.cvc4BinDir + os.sep + "cvc4.exe" ]
    CommandLineOptions.whoopEngineOptions += [ "/proverOpt:LOGIC=" + CommandLineOptions.logic ]
    CommandLineOptions.whoopCruncherOptions += [ "/proverOpt:LOGIC=" + CommandLineOptions.logic ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/proverOpt:LOGIC=" + CommandLineOptions.logic ]
    CommandLineOptions.whoopEngineOptions += [ "/useArrayTheory" ]
    CommandLineOptions.whoopCruncherOptions += [ "/useArrayTheory" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/useArrayTheory" ]
  else:
    CommandLineOptions.whoopEngineOptions += [ "/z3exe:" + findtools.z3BinDir + os.sep + "z3.exe" ]
    CommandLineOptions.whoopCruncherOptions += [ "/z3exe:" + findtools.z3BinDir + os.sep + "z3.exe" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/z3exe:" + findtools.z3BinDir + os.sep + "z3.exe" ]

  if CommandLineOptions.generateSmt2:
    CommandLineOptions.whoopCruncherOptions += [ "/proverLog:" + smt2Filename ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/proverLog:" + smt2Filename ]

  if CommandLineOptions.printPairs:
    CommandLineOptions.whoopEngineOptions += [ "/printPairs" ]
  if CommandLineOptions.debugging:
    CommandLineOptions.whoopEngineOptions += [ "/debugWhoop" ]
    CommandLineOptions.whoopCruncherOptions += [ "/debugWhoop" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/debugWhoop" ]

  if not os.getcwd() + os.sep in filename: filename = os.getcwd() + os.sep + filename
  CommandLineOptions.whoopEngineOptions += [ "/originalFile:" + filename + ext ]
  CommandLineOptions.whoopCruncherOptions += [ "/originalFile:" + filename + ext ]
  CommandLineOptions.whoopRaceCheckerOptions += [ "/originalFile:" + filename + ext ]

  if CommandLineOptions.onlyRaces:
    CommandLineOptions.whoopEngineOptions += [ "/onlyRaceChecking" ]

  CommandLineOptions.whoopEngineOptions += [ "/inlineBound:" + str(CommandLineOptions.inlineBound) ]
  CommandLineOptions.whoopCruncherOptions += [ "/inlineBound:" + str(CommandLineOptions.inlineBound) ]

  CommandLineOptions.corralOptions += [ "/k:" + str(CommandLineOptions.k) ]
  CommandLineOptions.corralOptions += [ "/recursionBound:" + str(CommandLineOptions.recursionBound) ]
  if CommandLineOptions.staticLoopBound > 0:
    CommandLineOptions.corralOptions += [ "/maxStaticLoopBound:" + str(CommandLineOptions.staticLoopBound) ]

  if CommandLineOptions.checkInParamAliasing:
    CommandLineOptions.whoopEngineOptions += [ "/checkInParamAliasing" ]
  if CommandLineOptions.noExistentialOpts:
    CommandLineOptions.whoopEngineOptions += [ "/noExistentialOpts" ]

  if CommandLineOptions.analyseOnly != "":
    CommandLineOptions.whoopRaceCheckerOptions += [ "/analyseOnly:" + CommandLineOptions.analyseOnly ]

  if CommandLineOptions.timePasses:
    CommandLineOptions.whoopEngineOptions += [ "/timePasses" ]
    CommandLineOptions.whoopCruncherOptions += [ "/timePasses" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/timePasses" ]

  if CommandLineOptions.noInfer:
    CommandLineOptions.whoopEngineOptions += [ "/skipInference" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/skipInference" ]

  if CommandLineOptions.noHeavyAsyncCallsOptimisation:
    CommandLineOptions.whoopEngineOptions += [ "/noHeavyAsyncCallsOptimisation" ]

  if CommandLineOptions.findBugs:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/findBugs" ]
  if CommandLineOptions.skipNonRacyPairs:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/skipRaceFreePairs" ]
  if CommandLineOptions.yieldAll:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/yieldAll" ]
  elif CommandLineOptions.yieldCoarse:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/yieldCoarse" ]
    CommandLineOptions.corralOptions += [ "/cooperative" ]
  elif CommandLineOptions.yieldNoAccess:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/yieldNoAccess" ]
  else:
    CommandLineOptions.corralOptions += [ "/cooperative" ]

  if CommandLineOptions.optimizeCorral:
    CommandLineOptions.corralOptions += [ "/cooperative" ]
    CommandLineOptions.whoopRaceCheckerOptions += [ "/optimizeCorral" ]

  if CommandLineOptions.yieldRaceChecking:
    CommandLineOptions.whoopRaceCheckerOptions += [ "/yieldRaceChecking" ]

  CommandLineOptions.whoopCruncherOptions += [ "/contractInfer" ]

  CommandLineOptions.whoopEngineOptions += [ bplFilename ]
  CommandLineOptions.whoopCruncherOptions += [ bplFilename ]
  CommandLineOptions.whoopRaceCheckerOptions += [ bplFilename ]

  """ RUN CHAUFFEUR """
  if not CommandLineOptions.skip["chauffeur"]:
    runTool("chauffeur",
             [findtools.chauffeurDir + "/chauffeur"] +
             CommandLineOptions.chauffeurOptions +
             ["-I" + findtools.llvmLibDir + "/clang/3.5.2/include"] +
             [("-I" + str(o)) for o in clangCoreIncludes] +
             [("-I" + str(o)) for o in CommandLineOptions.includes],
             ErrorCodes.CLANG_ERROR,
             CommandLineOptions.componentTimeout)
  if CommandLineOptions.stopAtRe: return 0

  """ RUN CLANG """
  if not CommandLineOptions.skip["clang"]:
    runTool("clang",
             [findtools.llvmBinDir + "/clang"] +
             CommandLineOptions.clangOptions +
             [("-I" + str(o)) for o in CommandLineOptions.includes] +
             [("-D" + str(o)) for o in CommandLineOptions.defines],
             ErrorCodes.CLANG_ERROR,
             CommandLineOptions.componentTimeout)
  if CommandLineOptions.stopAtBc: return 0

  """ RUN SMACK """
  if not CommandLineOptions.skip["smack"]:
    runTool("smack",
            [findtools.smackBinDir + "/smack"] +
            CommandLineOptions.smackOptions,
            ErrorCodes.SMACK_ERROR,
             CommandLineOptions.componentTimeout)
    processBPL(bplFilename, infoFilename)
  if CommandLineOptions.stopAtBpl: return 0

  """ RUN WHOOP ENGINE """
  if not CommandLineOptions.skip["engine"]:
    runTool("whoopEngine",
            (["mono"] if os.name == "posix" else []) +
            [findtools.whoopBinDir + "/WhoopEngine.exe"] +
            CommandLineOptions.whoopEngineOptions,
            ErrorCodes.WHOOP_ERROR,
            CommandLineOptions.componentTimeout)
    if CommandLineOptions.stopAtEngine: return 0

  if not CommandLineOptions.noInfer:
    """ RUN WHOOP CRUNCHER """
    if not CommandLineOptions.skip["cruncher"]:
      runTool("whoopCruncher",
              (["mono"] if os.name == "posix" else []) +
              [findtools.whoopBinDir + "/WhoopCruncher.exe"] +
              CommandLineOptions.whoopCruncherOptions,
              ErrorCodes.WHOOP_ERROR,
              CommandLineOptions.componentTimeout)
      if CommandLineOptions.stopAtCruncher: return 0

  """ RUN WHOOP RACE CHECKER """
  if not CommandLineOptions.skip["raceChecker"]:
    runTool("whoopRaceChecker",
            (["mono"] if os.name == "posix" else []) +
            [findtools.whoopBinDir + "/WhoopRaceChecker.exe"] +
            CommandLineOptions.whoopRaceCheckerOptions,
            ErrorCodes.DRIVER_ERROR,
            CommandLineOptions.componentTimeout)
    if CommandLineOptions.stopAtRaceChecker: return 0

  if CommandLineOptions.findBugs:
      """ RUN CORRAL """
      runCorral(filename)

  """ SUCCESS - REPORT STATUS """
  if CommandLineOptions.silent:
    return 0

  print("Verified: " + ", ".join(CommandLineOptions.sourceFiles))
  print("(but absolutely no warranty provided)")

  return 0

def showTiming(exitCode):
  if CommandLineOptions.timeCSVLabel is not None:
    times = [ Timing.get(tool, 0.0) for tool in Tools ]
    total = sum(times)
    times.append(total)
    row = [ '%.3f' % t for t in times ]
    label = CommandLineOptions.timeCSVLabel
    if len(label) > 0: row.insert(0, label)
    if exitCode is ErrorCodes.SUCCESS:
      row.insert(1,'PASS')
    else:
      row.insert(1,'FAIL(' + str(exitCode) + ')')
    print(','.join(row))
  else:
    total = sum(Timing.values())
    print("Timing information (%.2f secs):" % total)
    if Timing:
      padTool = max([ len(tool) for tool in Timing.keys() ])
      padTime = max([ len('%.3f secs' % t) for t in Timing.values() ])
      for tool in Tools:
        if tool in Timing:
          print("- %s : %s" % (tool.ljust(padTool), ('%.3f secs' % Timing[tool]).rjust(padTime)))
    else:
      print("- no tools ran")

def handleTiming(exitCode):
  if CommandLineOptions.time:
    showTiming(exitCode)
  sys.stderr.flush()
  sys.stdout.flush()

""" In order to make the tool importable and
usable as a python module we need to clean
up the global variables.
"""
def _cleanUpGlobals():
  global CommandLineOptions
  CommandLineOptions = DefaultCmdLineOptions()

""" Entry point for the Whoop tool chain. It is responsible
for exception handling and for optionally running Whoop in
an interactive python console.
"""
def main(argv):
  def doCleanUp(timing, exitCode=ErrorCodes.SUCCESS):
    if timing:
      cleanUpHandler.register(handleTiming, exitCode)
    if __name__ != '__main__':
      cleanUpHandler.register(_cleanUpGlobals)
    cleanUpHandler.call()
    cleanUpHandler.clear()

  try:
    startToolChain(argv)
  except ReportAndExit as e:
    doCleanUp(timing=True, exitCode=e.getExitCode())
    raise
  except Exception:
    doCleanUp(timing=False, exitCode=0)
    raise

  doCleanUp(timing=True)
  return ErrorCodes.SUCCESS

""" Entry point for the Whoop script
"""
if __name__ == '__main__':
  # These are the exception error codes that won't be printed if they are thrown
  ignoredErrors = [ ErrorCodes.SUCCESS, ErrorCodes.DRIVER_ERROR ]

  try:
    main(sys.argv[1:])
  except ReportAndExit as e:
    if (not (e.getExitCode() in ignoredErrors)) or CommandLineOptions.debugging:
      if e.getExitCode() == ErrorCodes.COMMAND_LINE_ERROR:
        print('Whoop: {0}'.format(e.msg))
      else:
        print(str(e), file=sys.stderr)
    sys.exit(e.getExitCode())
  sys.exit(ErrorCodes.SUCCESS)
