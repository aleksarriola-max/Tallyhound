# Tallyhound: keeping an AI auditor honest

Finance teams lose money to a short list of familiar problems: an invoice paid twice, a supplier's bank details quietly changed, an order split into pieces to slip under an approval limit. Software can spot these, and language models can now read messy files well enough to help. The difficulty is trust. A model that invents a plausible quote, or an automated system that releases a payment on its own, is worse than no tool at all. Tallyhound is built around that problem rather than around the model.

## The rule: agents propose, people decide

Every check in Tallyhound, whether a fixed rule or an AI agent, can only propose a finding. Approving, rejecting, holding and releasing are buttons a person presses, and every press lands in an audit trail. When sign-in is on, segregation of duties applies: the person who ran an analysis cannot approve its findings. The trail is tamper-evident. Each entry carries a fingerprint of itself and the entry before it, so editing or deleting an old entry breaks the chain, and the exported workbook says whether the chain is intact.

## The quote check

The most important line of code is the least glamorous. A finding must quote its evidence, and it is shown only if that quote is an exact copy of the line at the stated position in the uploaded file. A model that hallucinates a line, or quotes the right line at the wrong position, has its finding dropped before anyone sees it. Column matching renames only the header, and invoice PDFs are turned into one text file with fixed line numbers, so the check keeps working on real-world inputs. The Guardrails page lets anyone edit a quote and watch it get blocked.

## The Skeptic

After the agents propose, a second agent tries to disprove each finding and labels it Confirmed or Doubtful. Small local models have a known failure here: the reasoning says "this clearly breaches the limit" and the verdict says Doubtful. Tallyhound detects that contradiction, asks once more with the model's own reasoning in front of it, and marks the finding Unclear for a person if it still disagrees with itself. The Skeptic never hides anything. It only informs the reviewer.

## Measured, not claimed

Claims about AI accuracy are cheap, so Tallyhound ships with a way to check them. A challenge generator writes a fresh fictional month of payments, approvals, vendors, expenses, contracts, invoice PDFs and a bank statement, plants problems in it, and records an answer key. The Scorecard grades any engine against that key: recall (how many planted problems it found), precision (how many of its findings were real), and for AI runs whether the Skeptic caught false alarms or wrongly doubted real ones. `scripts/benchmark.py` repeats this across many seeds and difficulties.

The honest result so far is that the fixed rules are very strong on problems shaped like their checks: 100% on medium challenges with no false alarms, and 25 of 26 on the sample company. Hard mode words three problems so that no fixed rule can see them, such as a "yoga classes for myself" expense or a tax ID written without its dash. That is the fair test of whether the AI adds value, and it has to be run on the machine with the model. The point of the Scorecard is that the answer comes from measurement, not from a demo.

## Built to be used

Uploads accept any CSV layout through column matching, with suggestions drawn from common accounting exports and a memory for headers seen before. Invoice PDFs are read and checked against approvals and the vendor master, including the classic fraud of changed bank details on an invoice. A bank statement is reconciled against recorded payments. A proposed payment run goes through an eight-check gate before any money leaves. Reviewers can give findings owners and notes, and can suppress a pattern they have judged acceptable. Repeated rejections just over a limit produce a suggestion to change the policy, which a person accepts or ignores.

Everything runs locally: Ollama, LM Studio or vLLM on the same machine, a Docker setup that keeps app and model on one server, and a scheduled folder check that writes a report and sends a Slack or email alert when something needs a person.

## What it is not

Tallyhound does not replace an auditor and does not move money. It is a careful assistant that shows its evidence, says how sure it is, measures its own accuracy, and leaves every decision to a person.
