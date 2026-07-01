//+------------------------------------------------------------------+
//|                                                    TestLite.mqh   |
//+------------------------------------------------------------------+
//
// TestLite.mqh
// =============
// Assistente de asserções mínimo, escrito à mão — deliberadamente NÃO
// adota MQLUnit/MTUnit (02-RESEARCH.md "Don't Hand-Roll": frameworks da
// comunidade acrescentam dependência do Strategy Tester ou de um
// watcher externo, sem benefício real a esta escala — uma dúzia de
// funções de risco). Este ficheiro basta-se a si próprio: sem
// dependência do Strategy Tester, sem watcher externo — apenas
// asserções em processo, com saída via Print().
//
// Uso típico (ver mql5/Tests/RiskGuardTests.mq5):
//     CTestLite test("RiskGuardTests");
//     test.AssertTrue(condicao, "mensagem descritiva");
//     test.AssertStringEquals("esperado", actual, "mensagem");
//     test.AssertNearDouble(1.23, valor, 0.0001, "mensagem");
//     test.PrintSummary();
//+------------------------------------------------------------------+
#property strict

class CTestLite
{
private:
   string m_suiteName;
   int    m_passed;
   int    m_failed;

public:
   //+------------------------------------------------------------------+
   //| Construtor — recebe o nome da suite para identificar o output    |
   //| no Journal quando várias suites correm na mesma sessão.           |
   //+------------------------------------------------------------------+
   CTestLite(string suiteName)
   {
      m_suiteName = suiteName;
      m_passed    = 0;
      m_failed    = 0;
      Print("=== Iniciando suite de testes: ", m_suiteName, " ===");
   }

   //+------------------------------------------------------------------+
   //| AssertTrue — asserção genérica sobre uma condição booleana.      |
   //+------------------------------------------------------------------+
   void AssertTrue(bool cond, string msg)
   {
      if(cond)
      {
         m_passed++;
         Print("  [PASS] ", msg);
      }
      else
      {
         m_failed++;
         Print("  [FAIL] ", msg);
      }
   }

   //+------------------------------------------------------------------+
   //| AssertStringEquals — compara duas strings exatamente (usado para |
   //| verificar a razão de rejeição exata devolvida por &rejectReason).|
   //+------------------------------------------------------------------+
   void AssertStringEquals(string expected, string actual, string msg)
   {
      bool cond = (expected == actual);
      if(cond)
      {
         m_passed++;
         Print("  [PASS] ", msg);
      }
      else
      {
         m_failed++;
         Print("  [FAIL] ", msg, " (esperado='", expected, "', obtido='", actual, "')");
      }
   }

   //+------------------------------------------------------------------+
   //| AssertNearDouble — compara dois doubles com tolerância, evitando |
   //| falsos negativos por erro de arredondamento em ponto flutuante.  |
   //+------------------------------------------------------------------+
   void AssertNearDouble(double expected, double actual, double tol, string msg)
   {
      bool cond = (MathAbs(expected - actual) <= tol);
      if(cond)
      {
         m_passed++;
         Print("  [PASS] ", msg);
      }
      else
      {
         m_failed++;
         Print("  [FAIL] ", msg, " (esperado=", DoubleToString(expected, 5),
               ", obtido=", DoubleToString(actual, 5), ", tol=", DoubleToString(tol, 5), ")");
      }
   }

   //+------------------------------------------------------------------+
   //| PrintSummary — imprime a contagem total/passou/falhou no Journal.|
   //| É a última chamada de qualquer Script que use esta classe.        |
   //+------------------------------------------------------------------+
   void PrintSummary()
   {
      int total = m_passed + m_failed;
      Print("=== Resumo da suite: ", m_suiteName, " ===");
      Print("  Total: ", total, "  Passou: ", m_passed, "  Falhou: ", m_failed);
      if(m_failed == 0)
         Print("  TODOS OS TESTES PASSARAM.");
      else
         Print("  ATENÇÃO: ", m_failed, " teste(s) falharam — ver detalhes acima.");
   }
};
//+------------------------------------------------------------------+
