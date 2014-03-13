﻿using System;
using System.IO;
using System.Collections.Generic;
using System.Diagnostics.Contracts;
using Microsoft.Boogie;

namespace whoop
{
  public class WhoopDriver
  {
    public static void Main(string[] args)
    {
      Contract.Requires(cce.NonNullElements(args));

      CommandLineOptions.Install(new WhoopCommandLineOptions());

      try {
        Util.GetCommandLineOptions().RunningBoogieFromCommandLine = true;

        if (!Util.GetCommandLineOptions().Parse(args)) {
          Environment.Exit((int) Outcome.FatalError);
        }
        if (Util.GetCommandLineOptions().Files.Count == 0) {
          whoop.IO.ErrorWriteLine("Whoop: error: no input files were specified");
          Environment.Exit((int) Outcome.FatalError);
        }

        List<string> fileList = new List<string>();

        foreach (string file in Util.GetCommandLineOptions().Files) {
          string extension = Path.GetExtension(file);
          if (extension != null) {
            extension = extension.ToLower();
          }
          fileList.Add(file);
        }

        foreach (string file in fileList) {
          Contract.Assert(file != null);
          string extension = Path.GetExtension(file);
          if (extension != null) {
            extension = extension.ToLower();
          }
          if (extension != ".wbpl") {
            whoop.IO.ErrorWriteLine("Whoop: error: {0} is not a .wbpl file", file);
            Environment.Exit((int) Outcome.FatalError);
          }
        }
          
        Outcome oc = new StaticLocksetAnalyser(fileList).Run();

        Environment.Exit((int) oc);
      } catch (Exception e) {
        Console.Error.Write("Exception thrown in Whoop: ");
        Console.Error.WriteLine(e);
        Environment.Exit((int) Outcome.FatalError);
      }
    }
  }
}
