﻿// ===-----------------------------------------------------------------------==//
//
//                 Whoop - a Verifier for Device Drivers
//
//  Copyright (c) 2013-2014 Pantazis Deligiannis (p.deligiannis@imperial.ac.uk)
//
//  This file is distributed under the Microsoft Public License.  See
//  LICENSE.TXT for details.
//
// ===----------------------------------------------------------------------===//

using System;
using System.Collections.Generic;
using System.Diagnostics.Contracts;
using System.Linq;
using Microsoft.Boogie;
using Microsoft.Basetypes;

using Whoop.Analysis;
using Whoop.Domain.Drivers;
using Whoop.Regions;

namespace Whoop
{
  public class AnalysisContext : CheckingContext
  {
    #region static fields

    private static Dictionary<EntryPoint, AnalysisContext> Registry =
      new Dictionary<EntryPoint, AnalysisContext>();

    private static Dictionary<PairCheckingRegion, Tuple<EntryPoint, EntryPoint>> PairRegistry =
      new Dictionary<PairCheckingRegion, Tuple<EntryPoint, EntryPoint>>();

    internal static HashSet<Lock> GlobalLocks = new HashSet<Lock>();

    #endregion

    #region fields

    public Program Program;
    public ResolutionContext ResContext;

    public List<Declaration> TopLevelDeclarations;

    internal List<InstrumentationRegion> InstrumentationRegions;
    internal List<Lock> Locks;
    internal List<Lockset> CurrentLocksets;
    internal List<Lockset> MemoryLocksets;

    internal Microsoft.Boogie.Type MemoryModelType;

    internal List<HashSet<string>> MatchedAccessesMap;
    internal Dictionary<string, HashSet<Expr>> AxiomAccessesMap;

    internal Constant DeviceStruct;

    internal Implementation Checker
    {
      get;
      private set;
    }

    #endregion

    #region public API

    public AnalysisContext(Program program, ResolutionContext rc)
      : base((IErrorSink)null)
    {
      Contract.Requires(program != null);
      Contract.Requires(rc != null);

      this.Program = program;
      this.ResContext = rc;

      this.InstrumentationRegions = new List<InstrumentationRegion>();
      this.Locks = new List<Lock>();
      this.CurrentLocksets = new List<Lockset>();
      this.MemoryLocksets = new List<Lockset>();

      this.MemoryModelType = Microsoft.Boogie.Type.Int;

      this.MatchedAccessesMap = new List<HashSet<string>>();
      this.AxiomAccessesMap = new Dictionary<string, HashSet<Expr>>();

      this.DeviceStruct = null;

      this.ResetToProgramTopLevelDeclarations();

      this.Checker = this.TopLevelDeclarations.OfType<Implementation>().ToList().
        FirstOrDefault(val => val.Name.Equals("whoop$checker"));
    }

    public void EliminateDeadVariables()
    {
      ExecutionEngine.EliminateDeadVariables(this.Program);
    }

    public void Inline()
    {
      ExecutionEngine.Inline(this.Program);
    }

    public void InlineEntryPoint(EntryPoint ep)
    {
      var impl = this.GetImplementation(ep.Name);

      impl.Proc.Attributes = new QKeyValue(Token.NoToken,
        "inline", new List<object>{ new LiteralExpr(Token.NoToken, BigNum.FromInt(1)) },
        impl.Proc.Attributes);
      impl.Attributes = new QKeyValue(Token.NoToken,
        "inline", new List<object>{ new LiteralExpr(Token.NoToken, BigNum.FromInt(1)) },
        impl.Attributes);

      ep.IsInlined = true;
    }

    public List<Implementation> GetCheckerImplementations()
    {
      return this.TopLevelDeclarations.OfType<Implementation>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "checker"));
    }

    public List<Procedure> GetEntryPoints()
    {
      return this.TopLevelDeclarations.OfType<Procedure>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "entrypoint"));
    }

    public List<Procedure> GetEntryPointHelpers()
    {
      return this.TopLevelDeclarations.OfType<Procedure>().ToList().
        FindAll(val => QKeyValue.FindStringAttribute(val.Attributes, "tag") != null);
    }

    public List<Variable> GetLockVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "lock"));
    }

    public List<Variable> GetCurrentLocksetVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "current_lockset"));
    }

    public List<Variable> GetMemoryLocksetVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "lockset"));
    }

    public List<Variable> GetWriteAccessCheckingVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "access_checking") &&
          val.Name.Contains("WRITTEN_"));
    }

    public List<Variable> GetReadAccessCheckingVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "access_checking") &&
          val.Name.Contains("READ_"));
    }

    public List<Variable> GetDomainSpecificVariables()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "domain_specific"));
    }

    public List<Variable> GetAccessWatchdogConstants()
    {
      return this.TopLevelDeclarations.OfType<Variable>().ToList().
        FindAll(val => QKeyValue.FindBoolAttribute(val.Attributes, "watchdog"));
    }

    public Implementation GetImplementation(string name)
    {
      Contract.Requires(name != null);
      var impl = (this.TopLevelDeclarations.FirstOrDefault(val => (val is Implementation) &&
        (val as Implementation).Name.Equals(name)) as Implementation);
      return impl;
    }

    public Constant GetConstant(string name)
    {
      Contract.Requires(name != null);
      var cons = (this.TopLevelDeclarations.FirstOrDefault(val => (val is Constant) &&
        (val as Constant).Name.Equals(name)) as Constant);
      return cons;
    }

    public Axiom GetAxiom(string name)
    {
      Contract.Requires(name != null);
      var axiom = (this.TopLevelDeclarations.FirstOrDefault(val => (val is Axiom) &&
        (val as Axiom).Expr.ToString().Equals("$isExternal(" + name + ")")) as Axiom);
      return axiom;
    }

    public bool IsAWhoopVariable(Variable v)
    {
      Contract.Requires(v != null);
      if (QKeyValue.FindBoolAttribute(v.Attributes, "lock") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "current_lockset") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "lockset") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "access_checking") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "existential") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "watchdog") ||
        QKeyValue.FindBoolAttribute(v.Attributes, "domain_specific"))
        return true;
      return false;
    }

    public bool IsAWhoopFunc(string name)
    {
      Contract.Requires(name != null);
      if (name.Contains("_UPDATE_CLS_") ||
        name.Contains("_WRITE_LS_") || name.Contains("_READ_LS_") ||
        name.Contains("_CHECK_WRITE_LS_") || name.Contains("_CHECK_READ_LS_") ||
        name.Contains("_NO_OP_") ||
        name.Contains("_DISABLE_NETWORK_") || name.Contains("_ENABLE_NETWORK_") ||
        name.Contains("_CHECK_ALL_LOCKS_HAVE_BEEN_RELEASED") ||
        name.Contains("_REGISTER_DEVICE_") || name.Contains("_UNREGISTER_DEVICE_"))
        return true;
      return false;
    }

    public bool IsCalledByAnyFunc(string name)
    {
      Contract.Requires(name != null);
      foreach (var ep in this.TopLevelDeclarations.OfType<Implementation>())
      {
        foreach (var block in ep.Blocks)
        {
          foreach (var cmd in block.Cmds)
          {
            if (cmd is CallCmd)
            {
              if ((cmd as CallCmd).callee.Equals(name))
                return true;
              foreach (var expr in (cmd as CallCmd).Ins)
              {
                if (!(expr is IdentifierExpr))
                  continue;
                if ((expr as IdentifierExpr).Name.Equals(name))
                  return true;
              }
            }
            else if (cmd is AssignCmd)
            {
              foreach (var rhs in (cmd as AssignCmd).Rhss)
              {
                if (!(rhs is IdentifierExpr))
                  continue;
                if ((rhs as IdentifierExpr).Name.Equals(name))
                  return true;
              }
            }
          }
        }
      }

      return false;
    }

    public bool IsImplementationRacing(Implementation impl)
    {
      Contract.Requires(impl != null);
      return SharedStateAnalyser.IsImplementationRacing(impl);
    }

    public int GetNumOfEntryPointRelatedFunctions(string name)
    {
      int counter = 0;

      foreach (var proc in this.TopLevelDeclarations.OfType<Procedure>())
      {
        if (QKeyValue.FindBoolAttribute(proc.Attributes, "entrypoint") ||
          (QKeyValue.FindStringAttribute(proc.Attributes, "tag") != null &&
            QKeyValue.FindStringAttribute(proc.Attributes, "tag").Equals(name)))
        {
          counter++;
        }
      }

      return counter;
    }

    public void ResetAnalysisContext()
    {
      this.Locks.Clear();
      this.CurrentLocksets.Clear();
      this.MemoryLocksets.Clear();
      this.TopLevelDeclarations = this.Program.TopLevelDeclarations.ToArray().ToList();
    }

    public void ResetToProgramTopLevelDeclarations()
    {
      this.TopLevelDeclarations = this.Program.TopLevelDeclarations.ToArray().ToList();
    }

    #endregion

    #region static public API

    public static AnalysisContext GetAnalysisContext(EntryPoint ep)
    {
      if (!AnalysisContext.Registry.ContainsKey(ep))
        return null;
      return AnalysisContext.Registry[ep];
    }

    public static void RegisterEntryPointAnalysisContext(AnalysisContext ac, EntryPoint ep)
    {
      if (AnalysisContext.Registry.ContainsKey(ep))
        AnalysisContext.Registry[ep] = ac;
      else
        AnalysisContext.Registry.Add(ep, ac);
    }

    internal static PairCheckingRegion GetPairAnalysisContext(EntryPoint ep1, EntryPoint ep2)
    {
      if (AnalysisContext.PairRegistry.Any(val =>
        (val.Value.Item1.Equals(ep1) && val.Value.Item2.Equals(ep2)) ||
        (val.Value.Item1.Equals(ep2) && val.Value.Item2.Equals(ep1))))
      {
        return AnalysisContext.PairRegistry.First(val =>
          (val.Value.Item1.Equals(ep1) && val.Value.Item2.Equals(ep2)) ||
        (val.Value.Item1.Equals(ep2) && val.Value.Item2.Equals(ep1))).Key;
      }
      else
      {
        return null;
      }
    }

    internal static void RegisterPairEntryPointAnalysisContext(PairCheckingRegion region,
      EntryPoint ep1, EntryPoint ep2)
    {
      if (AnalysisContext.PairRegistry.ContainsKey(region))
        AnalysisContext.PairRegistry[region] = new Tuple<EntryPoint, EntryPoint>(ep1, ep2);
      else
        AnalysisContext.PairRegistry.Add(region, new Tuple<EntryPoint, EntryPoint>(ep1, ep2));
    }

    #endregion

    #region internal helper functions

    internal string GetWriteAccessVariableName(EntryPoint ep, string name)
    {
      return "WRITTEN_" + name + "_$" + ep.Name;
    }

    internal string GetReadAccessVariableName(EntryPoint ep, string name)
    {
      return "READ_" + name + "_$" + ep.Name;
    }

    internal string GetAccessWatchdogConstantName(string name)
    {
      return "WATCHED_ACCESS_" + name;
    }

    #endregion
  }
}
