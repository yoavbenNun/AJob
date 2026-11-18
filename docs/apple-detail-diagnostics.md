# Apple detail failure diagnostics

The existing worker reports aggregate detail failures and marks source coverage
incomplete. Its generic exception-class warnings do not identify the posting or
distinguish navigation from waiting for verified content.

Failures now additionally emit one bounded JSON warning per failed operation:

```json
{"event":"apple_detail_failure","posting_id":"200676939-0865","stage":"content_verification","category":"timeout","elapsed_ms":18005,"timeout_ms":18000,"navigation_completed":true,"content_verified":false,"http_status":200}
```

Stages: page_creation, navigation, response_validation, detail_deadline,
content_verification, final_validation, evidence_validation, cleanup.
Categories: timeout, http_error, validation_error, invalid_content,
unexpected_error, cancelled, cleanup_error.

Navigation completed means goto(commit) returned, not that the response was valid.
Content verified means Apple's identity/section verifier returned a valid snapshot;
it is not a persistence or relevance decision. A cleanup error after successful
enrichment may therefore report content_verified=true without changing success.
The per-detail budget is the existing timeout; elapsed time includes creation and
cleanup. These are not additional waits or a new source budget.

The unchanged parent process owns whole-source/whole-scan deadlines and records
source termination independently. Hard-killed workers cannot reliably execute
finally or emit a final per-detail diagnostic. Python cancellation is logged and
re-raised; diagnostics cannot provide a receipt for forced process termination.

No URLs, titles, response bodies, exception messages, cookies or credentials are
serialized. Posting IDs come only from the existing Apple URL validator and are
length bounded. Diagnostic emission is best effort and cannot change write guards,
returned errors, retries or cancellation. Existing generic warnings remain for
compatibility; the JSON warning supplies the missing phase and identity.

A content-verification timeout does not establish why a particular check failed.
Use the next scheduled logs to identify affected IDs and stages before proposing
any collection repair. This instrumentation changes no verification conditions.
