# Post-Mortem: Auth Service Outage — 2026-04-22

**Severity:** P1  
**Duration:** 14:05 – 16:30 WIB (2h 25m)  
**Author:** Reza Firmansyah  
**Reviewed by:** Siti Rahayu, Budi Santoso

---

## Summary

On 2026-04-22, the auth service experienced a complete login failure affecting all users of the SIPGN portal. Root cause was a misconfigured Redis connection pool after a scheduled infrastructure maintenance window.

---

## Timeline

- **13:45** — Infra team (Budi Santoso) completed Redis cluster upgrade on staging
- **14:00** — Deployment pipeline promoted auth-service v2.3.1 to production automatically
- **14:05** — First alerts fired: login error rate >90% (Truewatch alert TW-20260422-003)
- **14:12** — On-call engineer (Reza Firmansyah) paged and began investigation
- **14:35** — Root cause identified: Redis max_connections misconfigured as 5 instead of 50
- **15:10** — Fix deployed: auth-service v2.3.2 with corrected pool config
- **15:20** — Error rate dropped below 1%
- **16:30** — Incident closed, monitoring confirmed stable

---

## Root Cause

The Redis connection pool `max_connections` was set to 5 in the production config template (`infrastructure/redis/prod.conf`). This value was accidentally overwritten during the Redis upgrade by Budi Santoso. Under normal load, the auth service requires 20-40 concurrent Redis connections.

---

## Impact

- All portal users unable to log in for 2h 25m
- ~3,400 login attempts failed
- No data loss or corruption

---

## Action Items

- [ ] **Reza Firmansyah**: Add Redis connection pool size to deployment checklist by 2026-04-30
- [ ] **Siti Rahayu**: Write integration test asserting pool size ≥ 20 before prod deploy — due 2026-05-07
- [ ] **Budi Santoso**: Add config drift detection to infra pipeline — due 2026-05-14
- [ ] **All**: Review all prod.conf templates for similar single-digit misconfiguration — due 2026-05-10

---

## Services Affected

- `auth-service` (primary)
- `portal-informasi-fe-sipgn` (downstream — login page broken)
- `sipgn-gateway` (token validation unavailable)

---

## Lessons Learned

1. Infrastructure maintenance windows should not overlap with auto-promotion deployment windows.
2. Redis pool config should be explicitly validated in CI before production promotion.
3. On-call escalation was too slow — 7 minutes to page was too long for a P1.
