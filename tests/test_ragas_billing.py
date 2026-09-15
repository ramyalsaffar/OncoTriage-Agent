"""Offline Ragas wire-attempt billing. Run as a script; no provider/model loads."""
import asyncio
import copy
import os
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotriage import config, spend, spend_journal, provider_resilience as pr
from oncotriage.evaluation import ragas_harness as rh
from openai.types.completion_usage import CompletionUsage

JUDGE = "gpt-5.6-terra"
EMBED = "text-embedding-3-small"

class APITimeoutError(Exception):
    pass

class StatusError(Exception):
    def __init__(self, status):
        self.status_code = status

class Pacer:
    def reserve(self, *a, **kw):
        return types.SimpleNamespace(waited_s=0, start=0, slot=None)
    def wait(self, *a, **kw):
        pass
    def settle(self, *a, **kw):
        pass

async def no_sleep(*a, **kw):
    return True


def response(kind, *, write=20, prompt=100, cached=30):
    if kind == "judge":
        details = dict(cached_tokens=cached)
        if write is not None:
            details["cache_write_tokens"] = write
        usage = CompletionUsage.model_validate(dict(prompt_tokens=prompt,
            completion_tokens=10, total_tokens=prompt+10,
            prompt_tokens_details=details))
    else:
        usage = types.SimpleNamespace(total_tokens=100)
    return types.SimpleNamespace(model=JUDGE if kind == "judge" else EMBED,
                                 service_tier="default", usage=usage)

class BillingTests(unittest.TestCase):
    def setUp(self):
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        for obj, name, val in ((spend, 'SPEND_LEDGER', spend.SpendLedger()),
                              (config, 'SPEND_CAP_USD', 100.0),
                              (config, 'SPEND_CAP_ENFORCED', True),
                              (config, 'MATCHING_CALL_MAX_ATTEMPTS', 3),
                              (pr, 'PACER', Pacer()),
                              (pr, 'cancellable_wait_async', no_sleep)):
            self.stack.enter_context(patch.object(obj, name, val))
        spend.SPEND_STOP.reset()
        spend.BILLING_RECORD.clear()
        self.stack.enter_context(patch.object(rh, 'resolve_api_key', return_value='offline'))
        self.stack.enter_context(patch.object(rh, '_refuse_unpaced_scope'))
        self.stack.enter_context(patch.object(rh, 'ragas_maps_reasoning_params', return_value=False))

    def wrapper(self, kind, actions, tally=None):
        tally = tally or rh.UsageTally(JUDGE, EMBED)
        reached = []
        async def create(*a, **kw):
            reached.append(copy.deepcopy(kw))
            action = actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            if callable(action):
                return await action()
            return action
        client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)),
            embeddings=types.SimpleNamespace(create=create))
        def llm_factory(model, provider, client, **kwargs):
            return types.SimpleNamespace(model_args=kwargs,
                _map_provider_params=lambda: kwargs)
        llms = types.ModuleType('ragas.llms'); llms.llm_factory = llm_factory
        embeds = types.ModuleType('ragas.embeddings')
        embeds.embedding_factory = lambda *a, **kw: client
        with patch('openai.AsyncOpenAI', return_value=client), patch.dict(sys.modules,
                {'ragas.llms': llms, 'ragas.embeddings.base': embeds}):
            if kind == 'judge':
                rh.build_judge(JUDGE, None, 4096, tally, 0)
                fn = client.chat.completions.create
                kwargs = dict(model=JUDGE, messages=[{'role':'user','content':'synthetic'}],
                              max_completion_tokens=4096)
            else:
                rh.build_embeddings(EMBED, tally)
                fn = client.embeddings.create
                kwargs = dict(model=EMBED, input=['synthetic'])
        return fn, kwargs, tally, reached

    def test_failed_then_success_each_wrapper(self):
        for kind in ('judge', 'embedding'):
            with self.subTest(kind=kind):
                spend.SPEND_LEDGER.reset()
                fn, kw, tally, reached = self.wrapper(kind, [APITimeoutError(), response(kind)])
                expected = copy.deepcopy(kw)
                if kind == 'judge': expected['service_tier'] = 'default'
                reserve = rh.ragas_attempt_bound(kind, kw['model'], (), kw)[2]
                asyncio.run(fn(**kw))
                measured = 0.000276 if kind == 'judge' else 0.000002
                self.assertEqual(reached, [expected, expected])
                self.assertAlmostEqual(spend.SPEND_LEDGER.measured, reserve+measured)
                cost = tally.cost(JUDGE, EMBED)
                self.assertAlmostEqual(cost['total_usd'], measured)
                self.assertAlmostEqual(cost['unconfirmed_liability_usd'][kind], reserve)
                self.assertEqual(tally.attempt_outcomes[kind], {'possibly_billed':1, 'response':1})
                self.assertEqual(spend.SPEND_LEDGER.held_count(), 0)

    def test_unbilled_and_predispatch_each_wrapper(self):
        for kind in ('judge','embedding'):
            for status in (400, 429):
                with self.subTest(kind=kind,status=status):
                    spend.SPEND_LEDGER.reset()
                    fn, kw, tally, reached = self.wrapper(kind, [StatusError(status)]*3)
                    with self.assertRaises(StatusError): asyncio.run(fn(**kw))
                    self.assertEqual(spend.SPEND_LEDGER.measured, 0)
                    self.assertEqual(len(reached), 3 if status==429 else 1)
            spend.SPEND_LEDGER.charge_usd(200, spend.SPEND_SOURCE_RAGAS_JUDGE)
            fn, kw, tally, reached = self.wrapper(kind, [response(kind)])
            with self.assertRaises(spend.SpendLimitReached): asyncio.run(fn(**kw))
            self.assertFalse(reached)
            self.assertFalse(tally.attempt_outcomes[kind])
            spend.SPEND_STOP.reset()
            spend.SPEND_LEDGER.reset()

    def test_cancellation_and_missing_usage(self):
        for kind in ('judge','embedding'):
            for action, outcome in ((asyncio.CancelledError(), 'abandoned'),
                (types.SimpleNamespace(usage=None,service_tier='default'), 'response_unpriced')):
                spend.SPEND_LEDGER.reset()
                fn, kw, tally, reached = self.wrapper(kind, [action])
                try: asyncio.run(fn(**kw))
                except asyncio.CancelledError: pass
                bound = rh.ragas_attempt_bound(kind,kw['model'],(),kw)[2]
                self.assertAlmostEqual(spend.SPEND_LEDGER.measured,bound)
                self.assertEqual(tally.attempt_outcomes[kind],{outcome:1})
                self.assertEqual(tally.cost(JUDGE, EMBED)['total_usd'],0)
                self.assertEqual(spend.SPEND_LEDGER.held_count(),0)

    def test_sdk_extra_and_long_context(self):
        r = response('judge',prompt=300000,cached=200000,write=50000)
        self.assertEqual(r.usage.prompt_tokens_details.cache_write_tokens,50000)
        fn, kw, tally, _ = self.wrapper('judge',[r])
        asyncio.run(fn(**kw))
        self.assertAlmostEqual(spend.SPEND_LEDGER.measured,
                               50000*4e-6+50000*5e-6+200000*0.4e-6+10*18e-6)
        self.assertAlmostEqual(tally.cost(JUDGE, EMBED)['total_usd'],0.53018)

    def test_bounds_and_negative_controls(self):
        self.assertAlmostEqual(rh.ragas_attempt_bound('judge',JUDGE,(),
            dict(max_completion_tokens=4096))[2],5.323728)
        self.assertAlmostEqual(rh.ragas_attempt_bound('embedding',EMBED,(),
            dict(input=[[1,2],[3,4]]))[2],2*8192*0.02e-6)
        self.assertAlmostEqual(rh.ragas_attempt_bound('embedding',EMBED,(),
            dict(input=['x']*100))[2],0.006)
        for kind, model, kw in [('judge','unknown',{}), ('judge',JUDGE,{'n':2}),
             ('judge',JUDGE,{'max_completion_tokens':False}),
             ('embedding',EMBED,{'input':[{},'x']})]:
            with self.assertRaises(rh.RagasRefusal): rh.ragas_attempt_bound(kind,model,(),kw)
        fn, kw, tally, reached = self.wrapper('judge',[response('judge')])
        with self.assertRaises(rh.RagasRefusal): asyncio.run(fn(**kw,service_tier='priority'))
        self.assertFalse(reached)
        # Disable settlement: the monetary assertion detects the missing hook.
        with patch.object(rh._RagasAttemptRecord,'response',lambda *a: None):
            asyncio.run(fn(**kw))
        self.assertEqual(spend.SPEND_LEDGER.measured,0)
        self.assertGreater(spend.SPEND_LEDGER.held_count(),0)

    def test_checkpoint_resume_and_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'spend.jsonl')
            cp = spend_journal.RunSpendCheckpointer(spend.SPEND_BUDGET_CAMPAIGN,
                spend.SPEND_SOURCE_RAGAS_JUDGE,tmp,'unit',JUDGE,path=path,min_usd=0,
                accounting_basis='measured_responses_plus_unconfirmed_liability')
            tally = rh.UsageTally(JUDGE,EMBED); tally.checkpointer=cp
            fn,kw,_,_ = self.wrapper('judge',[APITimeoutError(),response('judge')],tally)
            asyncio.run(fn(**kw)); cp.finalize(spend.SPEND_LEDGER.measured)
            charged = spend.SPEND_LEDGER.measured
            seed = spend_journal.total(spend.SPEND_BUDGET_CAMPAIGN,path=path)
            spend.SPEND_LEDGER.reset(); spend.SPEND_LEDGER.seed(seed)
            self.assertAlmostEqual(spend.SPEND_LEDGER.total,charged)
            self.assertIn('measured_responses_plus_unconfirmed_liability',Path(path).read_text())
            tally.checkpointer = types.SimpleNamespace(checkpoint=lambda v: False,
                pending=(1,),conflicted=(),unconfirmed=1)
            fn,kw,_,reached=self.wrapper('embedding',[response('embedding')],tally)
            with self.assertRaises(rh.RagasRefusal): asyncio.run(fn(**kw))
            self.assertFalse(reached)

    def test_pacing_and_between_retry_cancellation(self):
        for kind in ('judge', 'embedding'):
            for phase in ('pacing', 'backoff'):
                spend.SPEND_LEDGER.reset()
                fn, kw, tally, reached = self.wrapper(kind, [APITimeoutError()])
                def cancelled(*a, **kw):
                    raise asyncio.CancelledError()
                async def async_cancelled(*a, **kw):
                    raise asyncio.CancelledError()
                context = (patch.object(pr.PACER, 'wait', cancelled) if phase == 'pacing'
                           else patch.object(pr, 'cancellable_wait_async', async_cancelled))
                with context, self.assertRaises(asyncio.CancelledError):
                    asyncio.run(fn(**kw))
                self.assertEqual(len(reached), int(phase == 'backoff'))
                bound = rh.ragas_attempt_bound(kind, kw['model'], (), kw)[2]
                self.assertAlmostEqual(spend.SPEND_LEDGER.measured,
                                       bound if phase == 'backoff' else 0)
                self.assertEqual(spend.SPEND_LEDGER.held_count(), 0)

    def test_concurrent_holds(self):
        for kind in ('judge', 'embedding'):
            spend.SPEND_LEDGER.reset()
            async def drive():
                entered, release = asyncio.Event(), asyncio.Event()
                async def blocked():
                    entered.set()
                    await release.wait()
                    return response(kind)
                fn, kw, tally, reached = self.wrapper(kind, [blocked, response(kind)])
                bound = rh.ragas_attempt_bound(kind, kw['model'], (), kw)[2]
                with patch.object(config, 'SPEND_CAP_USD', bound * 1.5):
                    first = asyncio.create_task(fn(**kw))
                    await entered.wait()
                    self.assertEqual(spend.SPEND_LEDGER.held_count(), 1)
                    with self.assertRaises(spend.BudgetAdmissionDeclined):
                        await fn(**kw)
                    self.assertEqual(len(reached), 1)
                    release.set()
                    await first
                self.assertEqual(spend.SPEND_LEDGER.held_count(), 0)
                self.assertEqual(tally.attempt_outcomes[kind], {'response':1})
            asyncio.run(drive())

    def test_actual_checkpoint_failure_recovery_fresh_process(self):
        for kind in ('judge', 'embedding'):
            spend.SPEND_LEDGER.reset()
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp)/'journal.jsonl')
                cp = spend_journal.RunSpendCheckpointer(spend.SPEND_BUDGET_CAMPAIGN,
                    spend.SPEND_SOURCE_RAGAS_JUDGE, tmp, 'retry', JUDGE,
                    path=path, min_usd=0,
                    accounting_basis='measured_responses_plus_unconfirmed_liability')
                tally = rh.UsageTally(JUDGE, EMBED); tally.checkpointer = cp
                fn, kw, _, reached = self.wrapper(kind, [APITimeoutError(),response(kind)], tally)
                with patch.object(spend_journal, 'append_with_outcome',
                                  return_value=spend_journal.APPEND_FAILED):
                    with self.assertRaises(rh.RagasRefusal): asyncio.run(fn(**kw))
                self.assertEqual(len(reached), 1)
                self.assertTrue(cp.pending)
                asyncio.run(fn(**kw))
                self.assertFalse(cp.pending)
                cp.finalize(spend.SPEND_LEDGER.measured)
                # Fresh interpreter, real journal reader and real seed owner.
                script = ("import sys,json; from oncotriage import spend,spend_journal; "
                          "spend.SPEND_LEDGER.seed(spend_journal.total("
                          "spend.SPEND_BUDGET_CAMPAIGN,path=sys.argv[1])); "
                          "print(json.dumps(spend.SPEND_LEDGER.total))")
                out = subprocess.check_output([sys.executable, '-c', script, path], text=True)
                self.assertAlmostEqual(json.loads(out.splitlines()[-1]),
                                       spend.SPEND_LEDGER.measured)
                self.assertEqual(len(Path(path).read_text().splitlines()), 2)

    def test_unpriced_reasons_and_zero_price(self):
        cases = []
        for field, value, reason in [('model', 'other', 'model_mismatch_or_absent'),
                                    ('service_tier', 'priority', 'tier_mismatch_or_absent')]:
            r=response('judge'); setattr(r, field, value); cases.append((r, reason))
        inconsistent = response('judge'); inconsistent.usage.total_tokens = 999
        cases.append((inconsistent, 'invalid_total_tokens'))
        cases += [(response('judge',write=None), 'cache_write_count_absent'),
                  (response('judge',write=90), 'invalid_cache_counts')]
        for r, reason in cases:
            spend.SPEND_LEDGER.reset()
            fn,kw,tally,_=self.wrapper('judge',[r]); asyncio.run(fn(**kw))
            self.assertEqual(tally.unpriced_reasons, {'judge:'+reason:1})
            self.assertEqual(tally.cost(JUDGE,EMBED)['total_usd'],0)
            self.assertAlmostEqual(spend.SPEND_LEDGER.measured,5.323728)
        token=spend.begin_billed_attempt(spend.SPEND_SOURCE_RAGAS_JUDGE,JUDGE,
            1,1,where='offline zero',reserved_usd=1)
        self.assertEqual(token.resolve('response',response_usd=0),0)
        self.assertEqual(token.resolved_outcome,'response')
        token=spend.begin_billed_attempt(spend.SPEND_SOURCE_RAGAS_JUDGE,JUDGE,
            1,1,where='offline over bound',reserved_usd=1)
        self.assertEqual(token.resolve('response',response_usd=2),2)
        self.assertGreater(spend.BILLING_RECORD_FAULTS['bound_exceeded:ragas_judge'],0)

    def test_explicit_price_once_and_invalid(self):
        for price in (0.25, float('nan'),-1,True):
            token=spend.begin_billed_attempt(spend.SPEND_SOURCE_RAGAS_JUDGE,JUDGE,
                1,1,where='offline',reserved_usd=1)
            amount=token.resolve('response',response_usd=price)
            self.assertEqual(amount,0.25 if price==0.25 else 1)
            before=spend.SPEND_LEDGER.measured
            token.resolve('response',response_usd=50)
            self.assertEqual(before,spend.SPEND_LEDGER.measured)

if __name__=='__main__': unittest.main()
