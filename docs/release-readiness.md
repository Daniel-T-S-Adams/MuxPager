# Release readiness

MuxPager is usable as a personal tool, but a full release needs an explicit
fault register. The important standard is not "nothing can go wrong"; it is
that failures are visible, recoverable, and do not silently send input to the
wrong place.

Use this file for issues that are bigger than ordinary bugs and should be
handled before describing the project as mature.

## Required before full release

| Area | Failure mode | Current expectation | Release target |
| --- | --- | --- | --- |
| Back-to-back questions | An agent asks another single-choice question immediately after the previous answer. | One active question per agent topic; a newer question supersedes the older one. This protects correctness but can be confusing. | Make the Telegram topic show a clear sequence: answered question, next question, and which answer is being delivered. Add a regression covering immediate follow-up questions. |
| Multiple simultaneous questions | Codex or Claude asks for more than one choice in the same turn. | Unsupported. MuxPager intentionally handles one question with two to six choices. | Reject or collapse unsupported multi-question prompts with a clear Telegram message and agent instruction. Do not guess answers. |
| Agent capacity/rate-limit stalls | A model reports capacity, billing, quota, network, or rate-limit trouble and cannot continue. | The pane may still look like a working live agent if the CLI does not emit a normal completion hook. | Detect common terminal states or hook failures and mark the topic as needing attention rather than indefinitely working. |
| Approval prompts | An agent asks for command, file, network, or MCP approval in the terminal. | Text and single-choice questions work remotely; native approval UIs may still require opening tmux. | Either surface approvals as Telegram buttons with the same safety labels, or explicitly detect and notify "approval required in tmux". |
| Long jobs | An agent runs a slow command, waits on I/O, or streams output for a long time. | Running, sleeping, and I/O-waiting agent process trees remain live and should not be treated as dead. | Keep this behavior covered by tests and add watchdog messaging that distinguishes "still running" from "possibly stuck". |
| Stale working state | Hooks miss the final state, the CLI crashes, or the bridge restarts at the wrong moment. | Process liveness prevents dead panes from staying as agents, but a live CLI can still be stuck in a stale working state. | Add a heartbeat or last-activity age to `/status`, sidebar details, and Telegram diagnostics. |
| Text delivery timing | Telegram text arrives while the pane is busy, at a shell prompt, or in an unexpected terminal UI. | The bridge revalidates that the mapped agent exists and rejects messages after exit. | Queue or reject based on an explicit idle/ready signal, and explain the reason in Telegram. Avoid blind paste into ambiguous states. |
| Rapid consecutive Telegram prompts | The user sends a second Telegram message before the bot has acknowledged, delivered, or received a reply to the first one. | Ordinary text is treated as a prompt for that topic, but the user intent can become ambiguous if messages arrive faster than the agent state changes. | Decide the product rule: reject while busy, queue in order with visible queued state, or require an explicit append/send command. Add tests that preserve order and avoid double-submit races. |
| Direct tmux versus Telegram race | The user answers locally while also tapping a Telegram button or sending Telegram text. | Choice answers are first-valid-answer-wins, and direct tmux answers mark the Telegram buttons inactive. | Add end-to-end race tests for local answer, Telegram button, and bridge restart ordering. |
| Telegram API failures | Telegram polling, message edits, callback answers, or topic operations fail temporarily. | The bridge retries through its polling loop and logs service errors. | Add explicit retry/backoff behavior, user-visible degraded status, and tests for transient Telegram failures. |
| Installer variance | Different Linux distributions, missing systemd user support, older tmux, or moved checkouts break setup. | The installer is idempotent and `scripts/doctor.sh` reports many machine issues. | Define the supported platform matrix and make unsupported environments fail with clear messages. |
| Security review | Remote phone input has the same practical authority as typing into the agent pane. | The bridge restricts one Telegram user, one private group, literal tmux buffer paste, and mode-`0600` state. | Review approval forwarding, stored state, logs, and recovery procedures before broader distribution. |

## Triage rules

- Track a fault here when it can cause silent misrouting, stale state, confusing
  Telegram behavior, or a remote action that needs a clear safety boundary.
- Move a fault to [troubleshooting](troubleshooting.md) only when the expected
  user action is known and documented.
- Remove a row only after the behavior is implemented, covered by a regression
  test where practical, and documented in the user-facing workflow.
