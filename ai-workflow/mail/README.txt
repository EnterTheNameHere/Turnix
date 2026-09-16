ACTANT COORDINATION MAIL
========================

This tree is the canonical recipient-based coordination mailbox described by
ai-workflow/coordination-protocol.txt.

Use the actant-integration branch explicitly for reads and writes regardless of the
implementation branch used by the sending or receiving workstream.

Routing:

    intake/                         owner not yet assigned
    integration/inbox/             outstanding mail for Integration
    integration/archive/           handled Integration mail
    materialization-test/inbox/    outstanding mail for materialization-test
    materialization-test/archive/  handled materialization-test mail
    evil-analysis/inbox/           outstanding mail for Evil analysis
    evil-analysis/archive/         handled Evil-analysis mail

Do not publish new messages under the legacy development/coordination/... mailbox.
