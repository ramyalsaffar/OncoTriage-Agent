######################################################################
# Stage 5 on Amazon Bedrock: the Converse translation (Anthropic Claude)
######################################################################

"""Translate the Stage 5 request onto Amazon Bedrock's Converse API.

THE FLAG IS OFF AND NOTHING HERE RUNS. ``config.MATCHING_PROVIDER`` is
``"openai"``; ``oncotriage/agent/evaluation.py:call_matching_model`` dispatches
on it and every statement in this module is unreachable under the default. No
Bedrock client is constructed, no credential is resolved, boto3 is not
imported, and the request the OpenAI client is handed is byte-for-byte the one
it was handed before this file existed. The twelve characterization fixtures
replay clean without recapture, which is the behavioural half of that claim;
``tests/test_agent_bedrock_anthropic_adapter.py`` section 1 is the structural
half.

THIS IS THE SECOND BEDROCK BRANCH, NOT A REPLACEMENT FOR THE FIRST.
``oncotriage/agent/bedrock_adapter.py`` translates onto the OpenAI-compatible
**Responses** API and serves ``MATCHING_PROVIDER = "bedrock"`` (GPT-5.6 Terra).
That branch is intact, reachable, and is the documented comparison arm. This
one serves ``MATCHING_PROVIDER = "bedrock_anthropic"`` (Claude Sonnet 4.6).
They share no code, and that is measured rather than stylistic: different
client library (boto3 vs the OpenAI SDK), different credential chain, different
request shape, different response shape, different error classes, different
degradation vocabulary.

WHICH API, AND WHY THE OTHER TWO CANDIDATES WERE REJECTED
---------------------------------------------------------
Sources read 2026-08-30 and named by page.

**THE ANTHROPIC MESSAGES API WAS NEVER A CANDIDATE FOR THIS MODEL, and the
brief that asked for this work assumed otherwise.** Two independent documented
facts, either of which alone settles it:

  1. The Claude Sonnet 4.6 model card
     (``model-card-anthropic-claude-sonnet-4-6.html``) carries an "Endpoints
     and APIs supported" section. ``bedrock-mantle`` is marked NOT SUPPORTED
     for this model outright, and on ``bedrock-runtime`` the API table reads
     Messages **no**, Responses **no**, Chat Completions **no**, Converse
     **yes**, Invoke **yes**. So the Messages API is not served for this model
     on either endpoint.
  2. ``structured-output.html``'s supported-API table names the Messages API
     explicitly and marks it **No**: "Anthropic Messages API on the
     bedrock-mantle endpoint ... The output_config.format parameter is
     rejected with a 400 error. To use structured outputs with Anthropic Claude
     models, send the request through the Converse API or the InvokeModel API
     on the bedrock-runtime endpoint." Stage 5 depends on strict structured
     output, so even where that API exists it cannot serve this pipeline.

**SO THE REAL CHOICE WAS CONVERSE versus InvokeModel, AND THE BRIEF'S DECIDING
CRITERION DOES NOT DISCRIMINATE.** The criterion given was that prompt caching
must be expressible. Both express it: ``prompt-caching.html`` documents
``cachePoint`` for Converse and ``cache_control`` for InvokeModel, and lists
Claude Sonnet 4.6 with 1,024 minimum tokens per checkpoint, 4 checkpoints, 5m
and 1h TTLs, and ``system``/``messages``/``tools`` as the fields that accept
them -- for both. Saying so plainly matters more than picking a winner and
dressing the criterion up afterwards. Four secondary reasons chose Converse:

  1. **botocore validates the request shape locally.** ``client.converse(...)``
     is a modeled operation: an unknown top-level key raises
     ``ParamValidationError`` BEFORE a signed request leaves the machine.
     ``invoke_model(body=json.dumps(...))`` sends an opaque blob, so every
     shape error is a round trip and a 400. This project's standing rule --
     ``config.validate_matching_provider_config()``'s own docstring -- is that
     a misconfiguration must name the constant to edit "rather than arriving as
     a 400 from a request that has already been signed". Converse is the API
     that extends that rule to the request body.
  2. **The cache counters are modeled fields of the API, not of a vendor body.**
     ``usage.cacheReadInputTokens`` / ``cacheWriteInputTokens`` / ``cacheDetails``
     are declared in the Converse response shape
     (``API_runtime_Converse.html``). ``inferences.llm_classifier_cached_input_
     tokens`` is the column that decides whether the per-trial design pays for
     itself, and reading it from a modeled API field is strictly more stable
     than reading it out of a JSON blob whose shape is versioned by the model
     vendor.
  3. **``stopReason`` is a modeled, closed, NINE-member vocabulary** --
     ``end_turn | tool_use | max_tokens | stop_sequence | guardrail_intervened
     | content_filtered | malformed_model_output | malformed_tool_use |
     model_context_window_exceeded``. It distinguishes a TRUNCATION from a
     MALFORMED DECODE, which is exactly the distinction a structured-output
     pipeline needs and which the Responses branch has to guess at (its own
     docstring records mapping an unrecognised ``incomplete`` reason to
     "length" and counting it).
  4. **``outputConfig.effort`` is a first-class Converse field** (boto3's
     ``converse()`` request syntax: ``outputConfig={'textFormat': {...},
     'effort': 'string'}``), so the Anthropic effort control has a home that is
     not ``additionalModelRequestFields``.

**WHAT CONVERSE COSTS, STATED RATHER THAN GLOSSED: THERE IS NO MODEL ECHO.**
The Converse response shape declares ``additionalModelResponseFields``,
``metrics``, ``output``, ``performanceConfig``, ``serviceTier``, ``stopReason``,
``trace`` and ``usage``, and no ``model``. InvokeModel would have returned the
native Anthropic body, which carries one. See THE MODEL ECHO below for what is
done about it and what is lost.

THE MAPPING, FIELD BY FIELD, WITH ITS CITATION
-----------------------------------------------
Left is what ``evaluation.call_matching_model`` sends today. Right is what this
module sends.

  model=MATCHING_MODEL
      -> modelId=config.matching_wire_model()
         (config.BEDROCK_ANTHROPIC_MATCHING_MODEL, default
         ``us.anthropic.claude-sonnet-4-6``)
      THE WIRE ID IS NOT THE PRICED/CONFIGURED NAME, exactly as on the
      Responses branch. The model card's Programmatic Access table gives the
      In-Region id as ``anthropic.claude-sonnet-4-6``, the geo ids as
      ``us.`` / ``eu.`` / ``au.`` / ``jp.``-prefixed and the global id as
      ``global.anthropic.claude-sonnet-4-6``.
      **AND IN-REGION IS NOT AVAILABLE IN MOST REGIONS, INCLUDING THIS
      PROJECT'S DEFAULT.** The card's regional table marks In-Region
      "not supported" in us-east-1 and in every US, APAC and Middle East
      Region; the ONLY Region where it is supported is eu-west-2 (London). So
      the bare id would be refused on the default configuration.
      ``config.validate_matching_provider_config()`` refuses it locally,
      naming the constant, rather than letting it arrive as a 400.

  messages=[{"role":"system",...},{"role":"user",...}]
      -> system=[{"text": system_prompt}, {"cachePoint": {...}}]
         messages=[{"role":"user","content":[{"text": user_prompt}]}]
      THE SYSTEM PROMPT BECOMES THE TOP-LEVEL ``system`` PARAMETER, which is
      where Converse puts it and where a ``cachePoint`` can follow it.
      ``prompt-caching.html``: "Cache checkpoints are processed in this order:
      tools -> system -> messages ... For best cache hit rates, place stable
      content (tools, system) before variable content (messages), and place
      cache checkpoints after the stable content." Stage 5's system message IS
      the stable prefix -- the instructions plus the whole patient record --
      and the per-trial design's entire affordability rests on N requests
      sharing it. So the breakpoint goes at the end of ``system`` and the
      per-trial user message follows it, uncached.

  max_completion_tokens=MATCHING_MAX_TOKENS
      -> inferenceConfig.maxTokens=MATCHING_MAX_TOKENS
      Value NOT recomputed. Sonnet 4.6's max output is 64K (model card) and
      MATCHING_MAX_TOKENS is 32,000, so the ceiling fits with room.

  reasoning_effort=MATCHING_REASONING_EFFORT
      -> NOT EXPRESSIBLE. DROPPED, COUNTED, LOGGED, AND SUBSTITUTED BY A
         SEPARATELY DECLARED CONTROL.
      The two vocabularies do not overlap. OpenAI's is
      ``none|minimal|low|medium|high`` and this project is calibrated at
      ``'none'``; Anthropic's controls are ``thinking`` (adaptive / disabled)
      and ``effort`` (low|medium|high|max), and ``'none'`` is a member of
      neither. Sending ``'none'`` as an effort would be a 400; mapping it to
      ``'low'`` would silently substitute a level, which
      ``bedrock_adapter.py``'s VERIFY-AT-GO-LIVE (1) already forbids in words
      for the other branch.
      So the OpenAI value is dropped and counted under
      ``reasoning_effort_not_expressible``, and what is SENT is
      ``config.BEDROCK_ANTHROPIC_THINKING`` (default ``"disabled"``, the
      honest translation of "spend no tokens on reasoning") through
      ``additionalModelRequestFields``, plus ``config.BEDROCK_ANTHROPIC_EFFORT``
      (default None -- omitted -- because effort is meaningless with thinking
      disabled) through ``outputConfig.effort``.
      **THIS IS A DIFFERENT JUDGE AND THE SUBSTITUTION IS NOT A CONFIG DETAIL.**
      config's note on MATCHING_REASONING_EFFORT records a measured 69.1%
      agreement behind the 'none' choice on gpt-5.6-terra. Nothing carries that
      measurement across a model change, and nothing here pretends it does.

  seed=MATCHING_SEED
      -> NOT EXPRESSIBLE. DROPPED, COUNTED, LOGGED.
      Converse's request shape has no ``seed``: the modeled top-level fields
      are additionalModelRequestFields, additionalModelResponseFieldPaths,
      guardrailConfig, inferenceConfig (maxTokens / stopSequences /
      temperature / topP), messages, outputConfig, performanceConfig,
      promptVariables, requestMetadata, serviceTier, system, toolConfig. There
      is no Anthropic ``seed`` parameter either. Counted under
      ``seed_not_expressible``, reaching the run-end degradation report, and
      logged once per process at WARNING.
      There is deliberately NO extra_body escape hatch here, unlike
      BEDROCK_SEND_SEED_IN_EXTRA_BODY on the Responses branch: an unknown key
      in a modeled boto3 call is a local ``ParamValidationError``, not a field
      the service might tolerate, so there is nothing for a probe to discover.

  response_format={"type":"json_schema",
                   "json_schema":{"name":..., "strict":True, "schema":{...}}}
      -> outputConfig.textFormat={
             "type": "json_schema",
             "structure": {"jsonSchema": {"schema": <JSON **STRING**>,
                                          "name": ...,
                                          "description": ...}}}
      **THE SCHEMA IS A SERIALIZED STRING, NOT A NESTED OBJECT**, and that is
      the single most easily-missed field in the whole translation. Both
      ``structured-output.html``'s Converse example and boto3's own
      ``converse()`` request syntax give ``schema`` as a string; the InvokeModel
      form gives it as an object. It is serialized here with
      ``sort_keys=False`` and no whitespace, from the SAME object
      ``build_response_format()`` produces -- unwrapped, never rebuilt, on the
      argument that module already makes for the Responses branch: two builders
      for one schema agree until the day they do not, and here the disagreement
      would be a judge constrained by a schema that is not the one the fixture
      recorded.
      **``strict`` HAS NO TARGET FIELD, AND ITS ABSENCE IS CHECKED RATHER THAN
      ASSUMED.** On Converse, ``outputConfig.textFormat`` IS the constrained
      decode -- "Amazon Bedrock validates the JSON schema ... compiles the
      grammar ... You receive standard inference responses with strict schema
      compliance" -- so there is no flag to set. Converse's ``strict`` lives on
      ``toolSpec``, a different mechanism this pipeline does not use. The chat
      form's ``strict: True`` is therefore not forwarded, and
      ``_text_format_param`` RAISES if that flag is ever flipped to False,
      because the Converse path would go on constraining a decode the caller
      had just asked not to constrain.
      **THE SHIPPED SCHEMA IS INSIDE BEDROCK'S SUPPORTED SUBSET, MEASURED
      RATHER THAN ASSUMED.** ``structured-output.html`` forbids recursive
      schemas, external ``$ref``, numerical constraints (minimum / maximum /
      multipleOf), string constraints (minLength / maxLength) and any
      ``additionalProperties`` other than ``false``. A walk over
      ``build_response_schema()`` finds NONE of them: it uses only object,
      array, string, number, string ``enum``, ``required`` and
      ``additionalProperties: false``. ``tests/test_agent_bedrock_anthropic_
      adapter.py`` section 2 re-derives that walk, so a schema edit that
      introduced one fails here rather than as a 400 mid-campaign.

  temperature
      -> STILL NOT SENT. MATCHING_TEMPERATURE is None and
      ``inferenceConfig.temperature`` is optional. Unchanged.

  timeout=config.get_matching_request_timeout()
      -> a ``botocore.config.Config`` on the CLIENT, not a per-call argument.
      An httpx.Timeout means nothing to botocore. See
      ``config.get_bedrock_anthropic_client()`` for the connect/read split and
      for why ``max_attempts`` is ``OPENAI_SDK_MAX_RETRIES + 1`` -- botocore
      counts TOTAL attempts where the OpenAI SDK counts retries, and that
      off-by-one is a doubled transport budget if it is got wrong.

  (new) store
      -> NO SUCH FIELD, AND NONE IS NEEDED. The Responses branch has to send
      ``store=False`` because that API's vendor default is to retain the
      request and response for 30 days, and the Stage 5 input is a rendered
      patient record. The Converse API reference states the opposite as its own
      contract: "Amazon Bedrock doesn't store any text, images, or documents
      that you provide as content. The data is only used to generate the
      response." So the retention decision is satisfied by the API rather than
      by a parameter.

  (new) serviceTier
      -> ``{"type": config.BEDROCK_ANTHROPIC_SERVICE_TIER}``, OMITTED by
      default, which IS Standard. The model card's Service Tiers table marks
      Standard and Reserved supported and Priority and Flex NOT supported for
      this model, and Reserved "is set at the account level rather than per
      request". So the only per-request values that can be correct are
      omit-or-"default", and the validator refuses the rest locally.

  (new) cachePoint
      -> ``{"type": "default", "ttl": config.BEDROCK_ANTHROPIC_CACHE_TTL}``,
      appended to ``system``. 5m by default, on the vendor's own advice:
      "If you have prompts that are used at a regular cadence (i.e., system
      prompts that are used more frequently than every 5 minutes), continue to
      use the 5-minute cache, since this will continue to be refreshed at no
      additional charge." A per-trial wave issues its calls at once, so 5m
      covers it and 1h would pay a higher write rate ($6.00/1M against
      $3.75/1M, AWS Marketplace, read 2026-08-30) for headroom nothing uses.

  (new) additionalModelResponseFieldPaths=["/model"]
      -> asks Converse to lift the underlying model response's ``model`` field
      into ``additionalModelResponseFields``. See THE MODEL ECHO.

THE RESPONSE COMES BACK AS A ChatCompletion
--------------------------------------------
``translate_response`` returns a real ``openai.types.chat.ChatCompletion``,
constructed by validation rather than faked, for the reason the Responses
branch already gives: every attribute Stage 5 reads exists with the type it
expects and a shape error surfaces here rather than thirty frames downstream.
Stage 5's post-call code is untouched.

**THE USAGE MAPPING IS NOT A RENAME, AND GETTING IT WRONG UNDER-REPORTS MONEY.**
The two APIs disagree about what the input count MEANS:

  OpenAI    ``prompt_tokens`` INCLUDES the cached tokens;
            ``prompt_tokens_details.cached_tokens`` is a SUBSET of it.
  Converse  the three counts are DISJOINT. ``prompt-caching.html``, verbatim:
            "When prompt caching is enabled, the ``inputTokens`` field
            represents only the non-cached input tokens (tokens that were not
            read from or written to the cache). To calculate the total input
            tokens sent in a request, use the following formula:
            ``total input tokens = inputTokens + cacheReadInputTokens +
            cacheWriteInputTokens``".

So a direct ``inputTokens -> prompt_tokens`` rename would, on every cache hit,
under-report Stage 5's input tokens by exactly the cached amount and therefore
under-price the run -- silently, in the direction that flatters the migration.
The translation restores the OpenAI convention with the vendor's own formula:

  response.choices[0].message.content   <- the concatenated ``text`` parts of
                                           output.message.content.
                                           ``reasoningContent`` blocks are
                                           SKIPPED: they are not visible output
                                           and Stage 5 parses this string as
                                           JSON.
  response.choices[0].message.refusal   <- DERIVED FROM ``stopReason``, because
                                           Converse has no refusal content
                                           block. ``guardrail_intervened`` and
                                           ``content_filtered`` set it; nothing
                                           else does. WITHOUT THIS the branch
                                           reintroduces the exact defect Stage
                                           5's refusal route was built to
                                           remove -- a decline read as a parse
                                           failure and retried twice more at
                                           full price against a deterministic
                                           block. See _STOP_REASON_REFUSALS.
  response.choices[0].finish_reason     <- derived; see below.
  response.usage.prompt_tokens          <- inputTokens + cacheReadInputTokens
                                           + cacheWriteInputTokens
  response.usage.completion_tokens      <- outputTokens
  response.usage.prompt_tokens_details
          .cached_tokens                <- cacheReadInputTokens
          .cache_write_tokens           <- cacheWriteInputTokens (an extra
                                           field the SDK model tolerates;
                                           nothing prices it yet, and it is
                                           carried rather than discarded so a
                                           future pass can)
  response.usage.completion_tokens_details
          .reasoning_tokens             <- NOT AVAILABLE. Converse reports no
                                           reasoning-token count. Absent rather
                                           than zero, because
                                           ``inferences.llm_classifier_
                                           reasoning_tokens`` distinguishes "no
                                           response reported it" (NULL) from
                                           "the model reasoned not at all" (0),
                                           and with thinking disabled the
                                           honest answer is the first.
  response.id                           <- the botocore request id, which is a
                                           real traceable value; Converse
                                           carries no response id of its own.
  response.model                        <- see THE MODEL ECHO.

**NOTHING IS DEFAULTED TO ZERO THAT WAS NOT REPORTED**, on the Responses
branch's rule. ``prompt_tokens`` and ``completion_tokens`` DO default to 0 and
the absence is COUNTED, because Stage 5 adds them unconditionally and a missing
count there is a broken response rather than an unreported measurement.

THE MODEL ECHO
--------------
**CONVERSE RETURNS NO ``model`` FIELD, SO ``MatchingModelMismatchError`` CANNOT
FIRE ON THIS BRANCH UNLESS THE ECHO IS RECOVERED.** That check exists because a
run half-served by another model is the confound this project removes, and it
is the one guarantee this API surface does not offer.

Three things are done about it, in order:

  1. THE ECHO IS ASKED FOR. ``additionalModelResponseFieldPaths=["/model"]``
     asks Converse to lift the underlying model response's ``model`` field into
     ``additionalModelResponseFields``. The API reference says an invalid
     pointer is a 400 and a valid pointer naming a field the model response
     does not carry "is ignored by Converse" -- so this costs nothing if
     unsupported and yields a genuine attestation if it works. Whether it works
     is VERIFY-AT-GO-LIVE (A3) and cannot be settled from documentation.
  2. WHEN IT DOES NOT ARRIVE, THE REQUESTED ID IS USED **AND THE SUBSTITUTION
     IS RECORDED** -- ``model_echo_unavailable``, on the run-end degradation
     report, plus one WARNING per process. It is NOT silently passed off as an
     attestation, and this docstring is the place a reader learns that
     ``inferences.matching_model`` on a ``bedrock_anthropic`` row is what was
     REQUESTED rather than what answered.
  3. PRICING IS UNAFFECTED, which is why substituting is tolerable at all:
     Bedrock bills for the model id the request named, so
     ``get_model_cost()`` keyed on the requested id is correct whatever the
     response says. What is lost is the attestation, not the arithmetic.

  THE ALTERNATIVE WAS REJECTED: feeding the requested id through and saying
  nothing would make ``MatchingModelMismatchError`` compare a value with
  itself -- a check that has stopped checking, which is the exact shape this
  project's own rules forbid on sight.

finish_reason, DERIVED FROM stopReason
---------------------------------------
  end_turn                       -> "stop"
  stop_sequence                  -> "stop"
  max_tokens                     -> "length"
  model_context_window_exceeded  -> "length"
  guardrail_intervened           -> "content_filter"
  content_filtered               -> "content_filter"
  malformed_model_output         -> "stop", COUNTED
  malformed_tool_use             -> "stop", COUNTED
  tool_use                       -> RAISES
  anything else / absent         -> "stop", COUNTED

  "length" IS THE LOAD-BEARING ONE: ``FINISH_REASON_LENGTH`` is the only value
  ``node_llm_classifier_evaluation`` branches on, and it drives the reactive
  truncation split. ``model_context_window_exceeded`` joins it because halving
  the chunk is the correct remedy for both.

  **THE TWO ``malformed_*`` VALUES MAP TO "stop" AND ARE COUNTED, AND THE
  ALTERNATIVE IS ARGUED RATHER THAN DISMISSED.** Raising would name the cause
  more precisely -- but ``call_matching_model_bedrock_anthropic`` raising AFTER
  a response arrived means Stage 5 takes its API-failure return, which records
  the tokens billed on EARLIER calls and loses this one's, and this response
  DID carry a usage block that was paid for. Mapping to "stop" hands the
  malformed text to the parser, which fails it, which takes the JSON-parse
  retry path -- the same budget, with the usage counted. The counter is what
  makes the cause visible; the parse is what fails. Note the asymmetry with the
  Responses branch, which refuses to map an UNFINISHED response to "stop": that
  is a response that never completed, this is one that completed badly.

  ``tool_use`` RAISES because Stage 5 sends no ``toolConfig`` at all, so it is
  unreachable unless the request was not the one this module built.

ERROR TAXONOMY
--------------
Classified by EXCEPTION CLASS NAME and by the modeled error code, WITHOUT
importing botocore -- so the classifier is a pure function of its argument and
is driven in the standing test with fabricated exceptions rather than with a
live failure. Never changes control flow: Stage 5 catches bare ``Exception``
and takes the same return whatever this says.

  ThrottlingException          429  throttling. Retried in-SDK by botocore's
                                    standard retry mode.
  ModelNotReadyException       429  also retried in-SDK (the API reference says
                                    "The AWS SDK will automatically retry the
                                    operation up to 5 times").
  AccessDeniedException        403  the model is not enabled for the account,
                                    or the credential lacks
                                    ``bedrock:InvokeModel`` on this model /
                                    inference profile.
  ResourceNotFoundException    404  wrong modelId, or an inference profile that
                                    does not exist in this Region.
  ValidationException          400  a bad request. THE EXPECTED SHAPE OF EVERY
                                    VERIFY-AT-GO-LIVE FAILURE BELOW.
  ModelErrorException          424  the model failed while processing.
  ModelTimeoutException        408  the model exceeded its own timeout.
  ServiceUnavailableException  503  retried in-SDK.
  InternalServerException      500  retried in-SDK.
  ParamValidationError          --  BOTOCORE-LOCAL: the request shape was
                                    refused before any network call. This is
                                    the class the Converse choice buys.
  NoCredentialsError,
  NoRegionError                 --  configuration, locally detected.
  EndpointConnectionError,
  ConnectTimeoutError           --  the endpoint was unreachable.
  ReadTimeoutError              --  the read budget fired.

VERIFY-AT-GO-LIVE
-----------------
Every line above is documentation that only a live call can confirm. These
extend ``bedrock_adapter.py``'s ten rather than replacing them -- that list
still governs the Responses branch -- and are lettered A1.. so a report can
name one unambiguously. ``bedrock_probe.py --provider bedrock_anthropic``
issues them; it is gated behind ``--i-understand-this-bills`` and this pass
did not run it.

 (A1) STRUCTURED OUTPUT IS HONOURED, AND THE SCHEMA IS ACCEPTED.
      The model card marks "Structured outputs" SUPPORTED for this model on
      bedrock-runtime, and the shipped schema is inside the documented subset
      (measured, above) -- so this is expected to pass. What cannot be settled
      from documentation is whether Bedrock's grammar compiler accepts THIS
      schema: the page warns that a first-time schema "compiles the grammar,
      which may take up to a few minutes", so the FIRST call may be far slower
      than the 300s read budget allows for.
      PROBE: sends the real Stage 5 schema, checks the call is accepted, parses
      the output against the schema, and TIMES it.
      IF IT 400s: the schema names a feature the compiler refuses; the error
      names it. IF IT TIMES OUT: raise the read budget for the first call only,
      or pre-warm the grammar once per schema version.
      **RANKED FIRST because it is the one failure that makes the branch
      useless rather than merely degraded.**

 (A2) THE CACHE ACTUALLY WARMS, AND THE USAGE ARITHMETIC IS RIGHT.
      Two identical-prefix calls; the second must report
      ``cacheReadInputTokens > 0``. The probe prints the WHOLE usage block, so
      a renamed field is visible rather than read as a zero -- and it prints
      the derived ``prompt_tokens`` beside the three raw counts so the
      disjointness formula is checked against a real response rather than
      against this docstring.
      IF THE CACHE DOES NOT WARM: the per-trial design costs
      MAX_TRIALS_FOR_EVALUATION x the full input price and NOTHING RAISES.
      **RANKED SECOND: it is the failure that costs money silently.**

 (A3) WHETHER ``additionalModelResponseFieldPaths=["/model"]`` RETURNS AN ECHO.
      PROBE: prints ``additionalModelResponseFields``.
      IF IT DOES: the echo is real and ``MatchingModelMismatchError`` is live on
      this branch. IF IT DOES NOT: it is inert, the degradation counter says so
      on every run, and that is the state this module ships in.

 (A4) ``thinking`` IS ACCEPTED THROUGH ``additionalModelRequestFields``, AND IN
      WHICH SHAPE. The Converse reference documents the FIELD but no vocabulary
      for it; ``{"type": "disabled"}`` is Anthropic's Messages-API shape.
      PROBE: issues the call at the configured value and prints
      ``additionalModelResponseFields`` and the usage block.
      IF IT 400s: try ``outputConfig.effort`` alone with the thinking object
      omitted (set BEDROCK_ANTHROPIC_THINKING = None), which is one edit.

 (A5) ``outputConfig.effort`` IS ACCEPTED ALONGSIDE ``textFormat``.
      Both live under ``outputConfig`` in boto3's request syntax, so this is
      expected -- but the two have never been documented together.
      PROBE: a second call with BEDROCK_ANTHROPIC_EFFORT set.

 (A6) PRICING. PRICING_CONFIG's ``global.`` row is MEASURED from the AWS
      Marketplace listing the model card names (prod-ffvjxvh4ltq64, read
      2026-08-30): $3.00 in / $15.00 out / $0.30 cache read / $3.75 cache
      write (5m) / $6.00 cache write (1h) per 1M tokens. **THE ``us.`` / ``eu.``
      / IN-REGION ROWS ARE INFERRED, NOT MEASURED** -- that listing publishes
      Global dimensions only, and the +10% geo premium is carried over from the
      pattern this project already recorded for GPT-5.6 Terra. PROBE: prints
      its own cost from those rows; compare against the console bill.
      IF THEY DIFFER: edit PRICING_CONFIG, which is the loud-failure mechanism
      working as designed.

 (A7) THE STOP REASON ON A DELIBERATE TRUNCATION IS ``max_tokens``.
      PROBE (``--probe-truncation``): one call at ``maxTokens=16``. The
      truncation split is armed by exactly this string.

 (A8) WHETHER ``serviceTier`` MAY BE OMITTED. The model card says Standard is
      "set ``service_tier: default`` or omit the field"; boto3's enum includes
      ``reserved``, which the card says is account-level. Omission is the
      shipped default and is exercised by every probe call.

 (A9) CREDENTIALS. boto3 reads ``AWS_BEARER_TOKEN_BEDROCK`` and the ordinary
      SigV4 chain; it does NOT read this project's
      ``ONCOTRIAGE_BEDROCK_API_KEY``. ``config.get_bedrock_anthropic_client()``
      REFUSES rather than silently ignoring one that is set alone. A short-term
      key still expires in at most 12 hours, which is shorter than a
      full-corpus run -- ``bedrock_adapter.py``'s item (9) applies unchanged.

(A10) FIRST-CALL LATENCY AND THE READ BUDGET. See (A1). The probe times every
      call and prints it beside MATCHING_REQUEST_TIMEOUT_SECONDS.

NOTHING IN THIS MODULE RUNS AT IMPORT. No client, no credential, no socket, no
file, and boto3 is imported inside the two functions that need it -- the same
third-party-in-a-function-body exemption ``import icd10`` and ``import torch``
carry, and the same one ``oncotriage/staging/s3_sync.py`` already uses for
boto3 so that the half of that tool which runs today works with boto3 absent.
``tests/test_package_invariants.py`` section 2 proves the import purity for
every module in the package and this one is in that sweep.
"""

import json
from collections import Counter
from typing import Dict, List, Optional, Tuple

from openai.types.chat import ChatCompletion

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent.response_schema import build_response_format
from oncotriage.observability import get_logger


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# The degradation record
# ---------------------------------------------------------------------------
#
# A MODULE-LEVEL Counter on AGE_PARSE_FAILURES' footing (item 11a), NOT a key
# in any result dict: the twelve characterization fixtures diff Stage 4's and
# Stage 5's dicts field by field, and a new field there means recapturing all
# twelve at live prices for something no stage reads.
#
# SEPARATE FROM `bedrock_adapter.BEDROCK_ADAPTER_DEGRADATIONS`, and that is a
# decision rather than an accident. The two branches degrade in different ways
# -- this one cannot express a reasoning effort and cannot obtain a model echo,
# neither of which is true of the Responses branch -- and a shared counter
# would make the run-end report unable to say WHICH branch had degraded. It is
# also what lets `oncotriage/degradation.py` bind two counter objects and
# report them under two names.
BEDROCK_ANTHROPIC_DEGRADATIONS = Counter()

DEGRADATION_SEED_DROPPED = "seed_not_expressible"
DEGRADATION_EFFORT_DROPPED = "reasoning_effort_not_expressible"
DEGRADATION_NO_MODEL_ECHO = "model_echo_unavailable"
DEGRADATION_MALFORMED_OUTPUT = "model_output_malformed"
DEGRADATION_UNKNOWN_STOP_REASON = "stop_reason_unrecognised"
DEGRADATION_NO_USAGE = "response_carried_no_usage"
DEGRADATION_NO_MESSAGE = "response_carried_no_message"
DEGRADATION_UNKNOWN_ERROR = "error_class_unrecognised"

DEGRADATION_KEYS = (
    DEGRADATION_SEED_DROPPED,
    DEGRADATION_EFFORT_DROPPED,
    DEGRADATION_NO_MODEL_ECHO,
    DEGRADATION_MALFORMED_OUTPUT,
    DEGRADATION_UNKNOWN_STOP_REASON,
    DEGRADATION_NO_USAGE,
    DEGRADATION_NO_MESSAGE,
    DEGRADATION_UNKNOWN_ERROR,
)
"""The closed vocabulary of this counter's keys. Declared so a reader can
branch on it exhaustively and so a typo at a bump site is catchable by test
rather than by producing a counter nobody reads -- the same argument
``deps.RESOLUTION_STATES`` carries."""

# REGISTERED IN `oncotriage/degradation.py`'s `_REGISTRY_SPEC`, NOT BY A
# `register()` CALL HERE, for the cycle reason `bedrock_adapter.py` records:
# that module imports `oncotriage.agent.evaluation`, which imports this module,
# so an import of `degradation` here would close the loop.


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BedrockConverseTranslationError(RuntimeError):
    """A Converse reply carried no answer this pipeline can use.

    A ``RuntimeError`` subclass rather than a ``ValueError``, on the
    ``UnknownModelPricingError`` / ``MatchingModelMismatchError`` precedent: a
    stray ``except ValueError`` around a JSON parse must not eat it.

    IT IS RAISED RATHER THAN TURNED INTO AN EMPTY ANSWER. Stage 5's own except
    catches it and takes the API-failure return, which records the tokens
    already billed, names the error, and evaluates nothing.
    """


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------

MODEL_ECHO_POINTER = "/model"
"""The JSON Pointer asked for through ``additionalModelResponseFieldPaths``.

A CONSTANT rather than a literal because two places read it -- the builder that
sends it and the translator that looks the answer up -- and a pointer spelled
two ways is a pointer that silently stops matching. See THE MODEL ECHO."""

MODEL_ECHO_FIELD = "model"
"""The key ``additionalModelResponseFields`` carries the answer under.

AWS documents the RESPONSE field as "a JSON Pointer object" without giving its
key shape for a one-segment pointer, so the translator accepts BOTH the bare
key and the pointer spelling -- see ``_model_echo``. That tolerance is a
measured unknown, not sloppiness, and (A3) is what settles it."""


def _cache_point() -> Optional[Dict]:
    """The ``cachePoint`` block appended to ``system``, or None to omit it.

    THE TTL IS SENT EXPLICITLY EVEN THOUGH 5m IS THE DEFAULT. An omitted
    ``ttl`` means 5m today; naming it means the request states what it wants,
    so a vendor default that moves cannot silently re-price a campaign at the
    1h write rate.
    """
    if config.BEDROCK_ANTHROPIC_CACHE_TTL is None:
        return None
    return {"type": "default", "ttl": config.BEDROCK_ANTHROPIC_CACHE_TTL}


def _text_format_param() -> Dict:
    """``outputConfig.textFormat`` built from ``build_response_format()``.

    Chat Completions: {"type":"json_schema","json_schema":{name,strict,schema}}
    Converse:         {"type":"json_schema",
                       "structure":{"jsonSchema":{schema:<STRING>,name,description}}}

    THE SCHEMA IS UNWRAPPED FROM THE CHAT FORM RATHER THAN BUILT A SECOND TIME,
    then SERIALIZED, because Converse's ``schema`` member is a String. See the
    module docstring.

    ``sort_keys=False`` deliberately: the chat form's key order is the order
    ``response_schema.py`` argues for at length, and re-sorting it here would
    make the string this branch sends differ from the object the other branch
    sends for no reason anybody chose. Separators are compact so the serialized
    form carries no whitespace nobody asked for.

    Raises:
        BedrockConverseTranslationError: if the chat-form builder stops
            producing the shape this unwraps, or if it ever stops asking for
            ``strict``. The second is not pedantry: Converse has no way to ask
            for an UNCONSTRAINED decode through this field, so a chat form with
            ``strict: False`` would be translated into a constrained call the
            caller had just asked not to make.
    """
    chat_form = build_response_format()
    inner = chat_form.get("json_schema")
    if not isinstance(inner, dict) or "schema" not in inner or "name" not in inner:
        raise BedrockConverseTranslationError(
            "build_response_format() no longer returns the Chat Completions "
            f"json_schema shape this adapter unwraps; got keys "
            f"{sorted(chat_form)}. Update _text_format_param() in "
            "oncotriage/agent/bedrock_anthropic_adapter.py.")

    if inner.get("strict") is not True:
        raise BedrockConverseTranslationError(
            f"build_response_format() returned strict={inner.get('strict')!r}. "
            "Converse's outputConfig.textFormat has no `strict` flag -- the "
            "field IS the constrained decode -- so this branch cannot express "
            "an unconstrained request and would silently constrain one the "
            "caller asked not to constrain. Decide what an unconstrained "
            "Stage 5 means on Bedrock before flipping that flag.")

    json_schema: Dict = {
        "schema": json.dumps(inner["schema"], sort_keys=False,
                             separators=(",", ":")),
        "name": inner["name"],
    }
    if config.BEDROCK_ANTHROPIC_SCHEMA_DESCRIPTION is not None:
        json_schema["description"] = config.BEDROCK_ANTHROPIC_SCHEMA_DESCRIPTION

    return {"type": "json_schema", "structure": {"jsonSchema": json_schema}}


def _output_config() -> Dict:
    """``outputConfig``: the structured-output format, plus effort when set.

    ONE OBJECT CARRIES BOTH, which is boto3's own request syntax
    (``outputConfig={'textFormat': {...}, 'effort': 'string'}``). ``effort`` is
    OMITTED rather than sent as None when unset: omission is what "the model's
    default" means, and ``None`` is a value botocore would serialize.
    """
    out: Dict = {"textFormat": _text_format_param()}
    if config.BEDROCK_ANTHROPIC_EFFORT is not None:
        out["effort"] = config.BEDROCK_ANTHROPIC_EFFORT
    return out


def _additional_model_request_fields() -> Optional[Dict]:
    """``additionalModelRequestFields``, or None to omit it entirely.

    The ONE escape hatch Converse offers for a model-specific parameter, and
    the only place Anthropic's ``thinking`` control can go. Returns None rather
    than an empty dict when nothing is configured, because an empty object is a
    field botocore would send and this pipeline's rule is that an omitted field
    and a field set to nothing are different requests.
    """
    if config.BEDROCK_ANTHROPIC_THINKING is None:
        return None
    return {"thinking": {"type": config.BEDROCK_ANTHROPIC_THINKING}}


def build_converse_request(system_prompt: str, user_prompt: str) -> Dict:
    """The complete kwargs for ``client.converse(**kwargs)``. PURE.

    Pure and separate from the call on purpose: it is what
    ``tests/test_agent_bedrock_anthropic_adapter.py`` compares field by field
    against a pinned expectation, and a translation proved only through a live
    call is a translation that cannot be tested without spending money. It
    imports no boto3, so it is testable on a machine where boto3 is not
    installed -- which is the machine this pass was written on.

    Validates the provider configuration FIRST, so a misconfiguration names the
    constant to edit rather than arriving as a 400 from a signed request.
    """
    config.validate_matching_provider_config()

    system: List[Dict] = [{"text": system_prompt}]
    # THE BREAKPOINT GOES AT THE END OF `system` AND NOWHERE ELSE. That is the
    # end of the stable prefix: the instructions plus the whole patient record,
    # byte-identical across a per-trial wave. A second checkpoint inside
    # `messages` would cache the per-trial text, which is different on every
    # call by construction -- a cache write per call at 1.25x input with no
    # read to follow it.
    point = _cache_point()
    if point is not None:
        system.append({"cachePoint": point})

    kwargs: Dict = {
        "modelId": config.matching_wire_model(),
        "system": system,
        "messages": [
            {"role": "user", "content": [{"text": user_prompt}]},
        ],
        "inferenceConfig": {"maxTokens": config.MATCHING_MAX_TOKENS},
        "outputConfig": _output_config(),
    }

    extra = _additional_model_request_fields()
    if extra is not None:
        kwargs["additionalModelRequestFields"] = extra

    # OMITTED, NOT SENT AS None. `serviceTier=None` is a value botocore would
    # reject; omission is what "Standard" means.
    if config.BEDROCK_ANTHROPIC_SERVICE_TIER is not None:
        kwargs["serviceTier"] = {"type": config.BEDROCK_ANTHROPIC_SERVICE_TIER}

    if config.BEDROCK_ANTHROPIC_REQUEST_MODEL_ECHO:
        kwargs["additionalModelResponseFieldPaths"] = [MODEL_ECHO_POINTER]

    # NEITHER DROP IS RECORDED HERE, and the split is the one
    # `build_bedrock_request` already argues: this function is documented PURE
    # and is driven directly by the tests, and a pure function that mutates a
    # module-level counter is neither. Both drops are properties of the
    # CONFIGURATION rather than of each request, so counting them per call
    # would put a five-figure number in the run-end degradation report on every
    # run of this provider, which makes that report's "all counters are zero"
    # signal mean nothing. They are counted once per process by
    # `_warn_dropped_parameters_once`.

    return kwargs


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------

FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"

STOP_REASON_TOOL_USE = "tool_use"

_STOP_REASON_TO_FINISH = {
    "end_turn": FINISH_STOP,
    "stop_sequence": FINISH_STOP,
    "max_tokens": FINISH_LENGTH,
    "model_context_window_exceeded": FINISH_LENGTH,
    "guardrail_intervened": FINISH_CONTENT_FILTER,
    "content_filtered": FINISH_CONTENT_FILTER,
}
"""Six of Converse's nine documented ``stopReason`` values, mapped.

The other three are handled by branches rather than by a table entry, each for
its own reason: ``tool_use`` RAISES, and the two ``malformed_*`` values map to
``stop`` while being COUNTED. See the module docstring."""

_STOP_REASON_MALFORMED = ("malformed_model_output", "malformed_tool_use")

_STOP_REASON_REFUSALS = ("guardrail_intervened", "content_filtered")
"""The two stop reasons that mean "the system declined", not "the model failed".

THEY BECOME A ``refusal`` ON THE TRANSLATED MESSAGE, and that is a correctness
fix rather than a nicety. Stage 5 has a dedicated refusal route -- see
``REFUSAL_ERROR_PREFIX`` in ``oncotriage/agent/evaluation.py`` -- which
TERMINATES instead of retrying, records ``llm_classifier_refusal``, and emits
``event="refusal"`` rather than a JSON parse error. Its own note records what it
was built to remove: before it existed a refusal was read as a parse failure and
RETRIED up to ``MAX_LLM_CLASSIFIER_RETRIES`` times "at full price, against a
model that had already declined -- three billed calls and a record that names
the wrong fault."

CONVERSE HAS NO REFUSAL CONTENT BLOCK, so a naive translation leaves
``message.refusal`` permanently None and reintroduces exactly that defect on
this branch: a guardrail block arrives as empty content, the parse fails, and
the request is re-sent twice more to a guardrail that will block it identically.
The fact IS expressible here -- it just arrives in ``stopReason`` rather than in
a content part -- so it is mapped.

WHY TERMINATING IS RIGHT FOR BOTH MEMBERS: a guardrail is deterministic on the
request, and the request Stage 5 would re-send is byte-identical. Retrying
spends money to be told no again."""

REFUSAL_TEXT = {
    "guardrail_intervened": (
        "Amazon Bedrock's guardrail intervened and the response was not "
        "returned (Converse stopReason 'guardrail_intervened'). The request "
        "is not retried: a guardrail is deterministic on the request, and "
        "Stage 5 would re-send a byte-identical one."),
    "content_filtered": (
        "The response was filtered before it was returned (Converse "
        "stopReason 'content_filtered'). The request is not retried, for the "
        "same reason a guardrail block is not."),
}
"""The refusal prose per stop reason. ONE OWNER, because Stage 5 stores the
first 300 characters of it in ``inferences.error`` and a string written twice
is a string that drifts. It names the raw ``stopReason`` so a reader of that
column can get back to the API fact without this file."""

STOP_REASONS_DOCUMENTED = tuple(_STOP_REASON_TO_FINISH) + _STOP_REASON_MALFORMED + (
    STOP_REASON_TOOL_USE,)
"""All nine values ``API_runtime_Converse.html`` declares for ``stopReason``.

Declared as one closed tuple so the standing test can assert this module
handles EVERY member -- a mapping table that silently stopped covering one
would otherwise look identical to one that covers them all."""


def _as_dict(obj) -> Dict:
    """A plain dict for a botocore response, a dict, or None.

    botocore returns plain dicts already, so this is mostly a None guard -- it
    exists so a test may hand this module literal dicts and so a stand-in
    client returning an object rather than a mapping degrades to something
    inspectable rather than raising an AttributeError thirty frames on.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump()
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def _finish_reason(stop_reason) -> str:
    """Map a Converse ``stopReason`` onto a Chat Completions finish_reason.

    Raises:
        BedrockConverseTranslationError: on ``tool_use``, which is unreachable
            for a request this module built -- Stage 5 sends no ``toolConfig``
            -- and therefore means the request was not the one it built.
    """
    mapped = _STOP_REASON_TO_FINISH.get(stop_reason)
    if mapped is not None:
        return mapped

    if stop_reason == STOP_REASON_TOOL_USE:
        raise BedrockConverseTranslationError(
            f"Bedrock returned stopReason {stop_reason!r}. Stage 5 sends no "
            f"toolConfig, so a tool-use stop means the request that was sent "
            f"is not the one this adapter builds. Stage 5 records this as an "
            f"API failure rather than manufacturing a verdict.")

    if stop_reason in _STOP_REASON_MALFORMED:
        BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_MALFORMED_OUTPUT] += 1
        log.warning(
            "Bedrock reported a malformed model output; the content is passed "
            "to the parser, which will fail it and take the parse-retry path, "
            "so the tokens this call was billed are still recorded",
            stage=5, event="bedrock_converse_malformed_output",
            reason=str(stop_reason), degraded=True,
            provider=config.MATCHING_PROVIDER)
        return FINISH_STOP

    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_UNKNOWN_STOP_REASON] += 1
    log.warning(
        "Bedrock returned an unrecognised stopReason; treating it as a normal "
        "stop, which sends the content to the parser rather than to the "
        "truncation splitter",
        stage=5, event="bedrock_converse_stop_reason_unrecognised",
        reason=str(stop_reason), degraded=True,
        provider=config.MATCHING_PROVIDER)
    return FINISH_STOP


def _content_text(output) -> str:
    """The visible text, walked out of ``output.message.content``.

    ``reasoningContent`` blocks are SKIPPED rather than concatenated: they are
    not visible output, Stage 5 parses this string as JSON, and folding a
    reasoning summary into it would corrupt every parse. Anything that is not a
    ``text`` block is skipped for the same reason -- this walk reads the ONE
    member it understands rather than everything it is handed.
    """
    message = _as_dict(_as_dict(output).get("message"))
    texts: List[str] = []
    for block in (message.get("content") or []):
        block_d = _as_dict(block)
        if "text" in block_d:
            texts.append(block_d.get("text") or "")

    joined = "".join(texts)
    # THE TEST IS ON THE JOINED RESULT, NOT ON THE BLOCK LIST, and the
    # difference is measurable: a reply carrying `[{"text": null}]` or
    # `[{"text": ""}]` produces exactly the same empty answer as one carrying no
    # blocks at all, and counting only the second would leave the first as a
    # patient that failed to parse with nothing on the run-end report to say
    # why. The counter is named "response_carried_no_message"; an empty message
    # is no message.
    if not joined:
        BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_NO_MESSAGE] += 1

    return joined


def _usage_block(usage) -> Dict:
    """Converse usage -> Chat Completions usage, WITH THE DISJOINTNESS FIXED.

    ``inputTokens`` on Converse EXCLUDES cached tokens; ``prompt_tokens`` on
    Chat Completions INCLUDES them. The vendor's own formula --
    ``total = inputTokens + cacheReadInputTokens + cacheWriteInputTokens`` --
    is applied here so that Stage 5's accumulator and ``get_model_cost()`` see
    the total they have always seen. See the module docstring; getting this
    wrong under-reports money on every cache hit, silently, in the direction
    that flatters the migration.

    NOTHING IS DEFAULTED TO ZERO THAT WAS NOT REPORTED, on the Responses
    branch's rule: an absent cache reading produces an absent
    ``prompt_tokens_details`` rather than one full of zeros, because Stage 5
    distinguishes "the response carried no cached-token reading" (NULL) from
    "the provider cached nothing" (0). ``prompt_tokens`` and
    ``completion_tokens`` DO default to 0 and the absence is COUNTED, because
    Stage 5 adds them unconditionally and a missing count there is a broken
    response rather than an unreported measurement.

    ``completion_tokens_details`` is NEVER emitted: Converse reports no
    reasoning-token count, and inventing a zero would tell
    ``inferences.llm_classifier_reasoning_tokens`` that the model reasoned not
    at all when what is true is that nothing measured it.
    """
    u = _as_dict(usage)
    if not u:
        BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_NO_USAGE] += 1

    non_cached = u.get("inputTokens") or 0
    completion_tokens = u.get("outputTokens") or 0

    cache_read = u.get("cacheReadInputTokens")
    cache_write = u.get("cacheWriteInputTokens")

    prompt_tokens = non_cached + (cache_read or 0) + (cache_write or 0)

    block: Dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        # DERIVED, not read from `totalTokens`. Converse's own totalTokens is
        # documented as "the tokens input to the model and the tokens generated
        # by the model", and against the disjointness note above it is not
        # stated whether it includes the cache terms. Deriving it from the two
        # values this block actually carries makes the three numbers agree with
        # each other by construction.
        "total_tokens": prompt_tokens + completion_tokens,
    }

    if cache_read is not None or cache_write is not None:
        details: Dict = {}
        if cache_read is not None:
            details["cached_tokens"] = cache_read
        if cache_write is not None:
            # An extra field the SDK's model tolerates. Nothing in this project
            # prices it yet -- PRICING_CONFIG takes an {input, output} pair --
            # and it is carried rather than discarded at the boundary so that a
            # future pass measuring cache-write cost has the number.
            details["cache_write_tokens"] = cache_write
        block["prompt_tokens_details"] = details

    return block


def _model_echo(response: Dict) -> Tuple[str, bool]:
    """``(model, echoed)`` -- the answering model, and whether it was attested.

    Converse carries no ``model`` field, so this looks in
    ``additionalModelResponseFields`` for the pointer the request asked for,
    accepting BOTH the bare key and the pointer spelling because AWS documents
    the response as "a JSON Pointer object" without giving its key shape for a
    one-segment pointer. When nothing arrives, the REQUESTED id is returned and
    ``echoed`` is False -- which the caller records rather than swallowing.
    """
    extra = _as_dict(response.get("additionalModelResponseFields"))
    for key in (MODEL_ECHO_FIELD, MODEL_ECHO_POINTER):
        value = extra.get(key)
        if isinstance(value, str) and value:
            return value, True
    return config.matching_wire_model(), False


def translate_response(response):
    """A Converse reply as the ``ChatCompletion`` Stage 5 already consumes.

    Returns a REAL ``openai.types.chat.ChatCompletion``, validated rather than
    faked, so every attribute the post-call code reads exists with the type it
    expects and a shape error surfaces HERE rather than thirty frames into the
    parse.

    Raises:
        BedrockConverseTranslationError: on a stop reason that carries no
            usable answer.
    """
    r = _as_dict(response)

    stop_reason = r.get("stopReason")
    finish = _finish_reason(stop_reason)
    content = _content_text(r.get("output"))
    # SET ONLY FOR THE TWO DECLINE REASONS. Everything else leaves it None,
    # which is what Stage 5's `_refusal_text` reads as "not refused" -- its own
    # docstring says an absent field must degrade to the old path rather than
    # to a new one.
    refusal = REFUSAL_TEXT.get(stop_reason)

    model, echoed = _model_echo(r)
    if not echoed:
        _warn_model_echo_once()

    return ChatCompletion.model_validate({
        # THE BOTOCORE REQUEST ID, which is a real value AWS support can trace,
        # rather than an empty string. Converse carries no response id of its
        # own; a ChatCompletion requires one, and inventing a value that cannot
        # be traced back to a request would be worse than carrying this.
        "id": _as_dict(r.get("ResponseMetadata")).get("RequestId") or "",
        "object": "chat.completion",
        # Converse reports no creation timestamp. 0 rather than `time.time()`,
        # because a timestamp this process made up is not a fact about the
        # response and nothing in this project reads the field.
        "created": 0,
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": finish,
            "logprobs": None,
            "message": {
                "role": "assistant",
                "content": content,
                # DERIVED FROM stopReason, because Converse has no refusal
                # content block. See _STOP_REASON_REFUSALS: leaving this None
                # would send a guardrail block down Stage 5's JSON-parse path
                # and buy three billed retries against a deterministic block.
                "refusal": refusal,
            },
        }],
        "usage": _usage_block(r.get("usage")),
    })


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

ERROR_THROTTLED = "throttled"
ERROR_NOT_READY = "model_not_ready"
ERROR_FORBIDDEN = "forbidden"
ERROR_NOT_FOUND = "not_found"
ERROR_VALIDATION = "validation"
ERROR_MODEL_ERROR = "model_error"
ERROR_TIMEOUT = "timeout"
ERROR_CONNECTION = "connection"
ERROR_SERVER = "server"
ERROR_CREDENTIALS = "credentials"
ERROR_LOCAL_PARAMS = "local_param_validation"
ERROR_TRANSLATION = "translation"
ERROR_UNCLASSIFIED = "unclassified"

ERROR_CATEGORIES = (
    ERROR_THROTTLED, ERROR_NOT_READY, ERROR_FORBIDDEN, ERROR_NOT_FOUND,
    ERROR_VALIDATION, ERROR_MODEL_ERROR, ERROR_TIMEOUT, ERROR_CONNECTION,
    ERROR_SERVER, ERROR_CREDENTIALS, ERROR_LOCAL_PARAMS, ERROR_TRANSLATION,
    ERROR_UNCLASSIFIED,
)
"""Closed. ``ERROR_UNCLASSIFIED`` is a member rather than a fallback nobody
named: an error this taxonomy does not recognise is a finding, and a category
it can be counted under is how it becomes one."""

_ERROR_NAME_TO_CATEGORY = {
    # The nine modeled Converse exceptions (API_runtime_Converse.html).
    "ThrottlingException": ERROR_THROTTLED,
    "ModelNotReadyException": ERROR_NOT_READY,
    "AccessDeniedException": ERROR_FORBIDDEN,
    "ResourceNotFoundException": ERROR_NOT_FOUND,
    "ValidationException": ERROR_VALIDATION,
    "ModelErrorException": ERROR_MODEL_ERROR,
    "ModelTimeoutException": ERROR_TIMEOUT,
    "ServiceUnavailableException": ERROR_SERVER,
    "InternalServerException": ERROR_SERVER,
    # botocore's own, raised locally or on the socket.
    "ParamValidationError": ERROR_LOCAL_PARAMS,
    "NoCredentialsError": ERROR_CREDENTIALS,
    "PartialCredentialsError": ERROR_CREDENTIALS,
    "NoRegionError": ERROR_CREDENTIALS,
    "EndpointConnectionError": ERROR_CONNECTION,
    "ConnectTimeoutError": ERROR_CONNECTION,
    "ConnectionClosedError": ERROR_CONNECTION,
    "ReadTimeoutError": ERROR_TIMEOUT,
}
"""Exception CLASS NAME -> category.

BY NAME RATHER THAN BY ``isinstance``, AND THAT IS THE DESIGN. Importing
botocore here would put a module-scope third-party import into a file whose
whole claim is that importing it costs nothing, and it would make this
classifier undrivable on a machine without boto3 -- which is the machine this
module was written on. Names are also what a modeled botocore exception is
IDENTIFIED by: botocore synthesises the service-specific classes at client
construction, so ``client.exceptions.ThrottlingException`` is not importable
from any module at all.

The cost is stated: a subclass of one of these under a different name falls
through to the error-code lookup and then to ``ERROR_UNCLASSIFIED``, which is
counted. That is the safe direction -- a category nobody named is a finding."""


def _error_code(exc: BaseException) -> Optional[str]:
    """The modeled error code out of a botocore ``ClientError``, or None.

    ``ClientError`` is the ONE class botocore raises for every modeled service
    error when the caller has not gone through ``client.exceptions.*``, and the
    code is the only thing that distinguishes a throttle from a validation
    failure inside it. Read defensively: the attribute is a plain dict and a
    stand-in exception need not carry it.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) and code else None


def classify_error(exc: BaseException) -> str:
    """Name what went wrong. Never changes control flow.

    NOTHING ABOUT STAGE 5's BEHAVIOUR DEPENDS ON THIS. Its except clause
    catches bare ``Exception`` and takes the same return whatever this says, so
    an error the taxonomy misreads costs a log field and nothing else. That is
    deliberate: a classifier that gated recovery would be a second retry policy
    disagreeing with botocore's.

    Two lookups, in order: the exception's own class name, then -- for a
    ``ClientError``, which is one class covering every modeled service error --
    the error code inside it.
    """
    if isinstance(exc, BedrockConverseTranslationError):
        return ERROR_TRANSLATION

    by_name = _ERROR_NAME_TO_CATEGORY.get(type(exc).__name__)
    if by_name is not None:
        return by_name

    code = _error_code(exc)
    if code is not None:
        by_code = _ERROR_NAME_TO_CATEGORY.get(code)
        if by_code is not None:
            return by_code

    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_UNKNOWN_ERROR] += 1
    return ERROR_UNCLASSIFIED


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

_DROPPED_WARNED = False
_MODEL_ECHO_WARNED = False


def _warn_dropped_parameters_once() -> None:
    """One WARNING and one counter bump per process for each dropped parameter.

    Once rather than per call for the reason ``_finish_reason_warned`` exists in
    evaluation.py: a per-call line on a 22,000-patient run is noise that buries
    the lines that matter. THE COUNTERS ARE BUMPED HERE RATHER THAN IN
    ``build_converse_request`` for the reason recorded there -- both drops are
    properties of the configuration, so 1 says everything 45,000 would, and a
    counter guaranteed non-zero on every run of a configuration makes the
    run-end report's CLEAN line worthless.
    """
    global _DROPPED_WARNED
    if _DROPPED_WARNED:
        return
    _DROPPED_WARNED = True

    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_SEED_DROPPED] += 1
    log.warning(
        "MATCHING_SEED is not expressible on the Converse API and is being "
        "dropped; Stage 5 is less reproducible on this provider than on "
        "OpenAI. There is no extra_body escape hatch on a modeled boto3 call, "
        "so this is not something a probe can turn back on.",
        stage=5, event="bedrock_converse_seed_dropped", degraded=True,
        provider=config.MATCHING_PROVIDER)

    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_EFFORT_DROPPED] += 1
    log.warning(
        "MATCHING_REASONING_EFFORT is not expressible in the Anthropic effort "
        "vocabulary and is being dropped; a separately declared control is "
        "sent instead. This is a DIFFERENT JUDGE and needs re-baselining -- "
        "the agreement measurement behind the configured effort was taken on "
        "another model and does not carry across.",
        stage=5, event="bedrock_converse_effort_dropped", degraded=True,
        provider=config.MATCHING_PROVIDER,
        # Both sides of the substitution, so the record says what was asked for
        # AND what was sent rather than only that something was dropped.
        reason=(f"configured={config.MATCHING_REASONING_EFFORT!r} "
                f"sent_thinking={config.BEDROCK_ANTHROPIC_THINKING!r} "
                f"sent_effort={config.BEDROCK_ANTHROPIC_EFFORT!r}"))


def _warn_model_echo_once() -> None:
    """One WARNING and one counter bump per process for the missing echo.

    Per process rather than per call for the same reason as above -- and the
    counter is what makes the state visible on the run-end report, so that a
    reader of ``inferences.matching_model`` on a ``bedrock_anthropic`` row can
    find out whether that string was attested or merely requested.
    """
    global _MODEL_ECHO_WARNED
    if _MODEL_ECHO_WARNED:
        return
    _MODEL_ECHO_WARNED = True
    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_NO_MODEL_ECHO] += 1
    log.warning(
        "Converse returned no model echo, so MatchingModelMismatchError cannot "
        "fire on this run: inferences.matching_model records the model that "
        "was REQUESTED rather than one that was attested. Pricing is "
        "unaffected -- Bedrock bills for the id the request named.",
        stage=5, event="bedrock_converse_model_echo_unavailable",
        degraded=True, provider=config.MATCHING_PROVIDER,
        model=config.matching_wire_model())


def call_matching_model_bedrock_anthropic(system_prompt: str, user_prompt: str):
    """Issue the Stage 5 request against Bedrock Converse; return a ChatCompletion.

    THE CALLER OWNS ERROR HANDLING, exactly as it does for the OpenAI path and
    for the Responses branch: this raises whatever the client raises and
    ``node_llm_classifier_evaluation``'s except turns it into a retry. The only
    thing added on the failure path is a log line naming the taxonomy category,
    emitted before the re-raise so the record exists whatever the caller does.

    THE CLIENT COMES FROM ``deps``, never from ``config`` directly, so a test
    harness installs
    ``deps.set_override(deps.BEDROCK_ANTHROPIC_CLIENT, stub)`` and this call
    site sees it -- the same grip ``OPENAI_CLIENT`` gives the two fixture
    harnesses today.
    """
    # BUILT FIRST, because building validates the provider configuration -- and
    # a run that is about to be refused for naming an unsupported service tier
    # should not first be told about the dropped seed.
    kwargs = build_converse_request(system_prompt, user_prompt)

    _warn_dropped_parameters_once()

    try:
        raw = deps.get_bedrock_anthropic_client().converse(**kwargs)
    except Exception as exc:
        log.error("Stage 5 Bedrock Converse request failed",
                  stage=5, status="error",
                  event="bedrock_converse_request_failed",
                  reason=classify_error(exc),
                  error_type=type(exc).__name__,
                  error_message=str(exc),
                  provider=config.MATCHING_PROVIDER)
        raise

    return translate_response(raw)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 30 2026

@author: ramyalsaffar
"""
