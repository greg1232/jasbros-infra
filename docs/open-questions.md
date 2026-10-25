# Open questions

Things the docs assume but that need a person to confirm. Each has a
default, which is what gets built if nobody objects.

| # | Question | Default |
|---|---|---|
| Q1 | **DHCP reservations** on the eero. **Deferred (2026-10-25): nobody has the eero credentials yet.** The eero is managed only through the eero phone app, and the account owner can add an admin under Settings → Network admins. gmk is MAC `b4:65:dc:0a:c8:51` | Until then, everything is designed so addresses can move (D11, D12). Once there's access, reserve gmk and each speaker; nothing else needs to change |
| Q2 | **gmk is on Wi-Fi.** `enp2s0` is down. Is a cable possible? Wi-Fi works for one node, but adds latency and drops to everything | Stay on Wi-Fi, with power save off. If a cable gets plugged in, `flannel-iface` and `node-ip` move to it |
| Q3 | **How big should `/` get?** The LVM volume is 98 GB of 477 GB | Grow to 200 GB and leave the rest free |
| Q4 | **Archive the GitHub hey-claude repo** after migration? | Yes, with a README pointer here, once step 1 has been verified on a real speaker |
| Q5 | **Hostnames on the LAN.** *Answered: no.* `gmk` and `gmk.local` don't resolve from the laptop, and hey-claude's `card.py` records that mDNS didn't work on this network either | Everything uses IPs, found by scanning for them (D11, D12) |
| Q6 | **Push instead of poll**, later? Speakers could POST a heartbeat to gmk, which would work across subnets and need no inventory | Poll for v1. It needs no changes on the speaker. Revisit at ~10 speakers |
| Q7 | **Port `deploy.py` to Ansible**, and how far? | Later, step by step, after the move. See [claude-speaker-migration.md](claude-speaker-migration.md#later-not-now) |
| Q8 | **Passwordless sudo** for `normal` on gmk, so provisioning can run without a prompt? | No. Prompt for it each time |
| Q10–Q13 | The display: night-time blanking, ending the tty1 session, what speakers may show, going back to the dashboard on its own | See [display.md](display.md#open-questions) |
