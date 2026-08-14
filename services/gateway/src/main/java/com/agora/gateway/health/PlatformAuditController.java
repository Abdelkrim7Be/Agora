package com.agora.gateway.health;

import com.agora.gateway.audit.AuditService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/** Admin-only: "is the platform healthy right now, across every instance." */
@RestController
@RequestMapping("/audit")
public class PlatformAuditController {

    private final PlatformAuditService auditService;
    private final AuditService actionAudit;

    public PlatformAuditController(PlatformAuditService auditService, AuditService actionAudit) {
        this.auditService = auditService;
        this.actionAudit = actionAudit;
    }

    public record RunSummary(Long id, String ranAt, String ranBy, String verdict, int instanceCount, int warningCount) {
        static RunSummary from(PlatformAuditRun run) {
            return new RunSummary(run.getId(), run.getRanAt().toString(), run.getRanBy(), run.getVerdict(),
                    run.getInstanceCount(), run.getWarningCount());
        }
    }

    @PostMapping("/run")
    public PlatformAuditService.AuditRunResult run(Authentication auth) {
        String actor = auth != null ? auth.getName() : null;
        PlatformAuditService.AuditRunResult result = auditService.run(actor);
        actionAudit.record(actor, actorRole(auth), "platform_audit_run", "POST", "/audit/run", null, result.verdict());
        return result;
    }

    @GetMapping("/runs")
    public List<RunSummary> history() {
        return auditService.history().stream().map(RunSummary::from).toList();
    }

    @GetMapping("/runs/{id}")
    public ResponseEntity<Map<String, Object>> detail(@PathVariable Long id) {
        return auditService.find(id)
                .map(run -> ResponseEntity.ok(Map.<String, Object>of(
                        "id", run.getId(), "ran_at", run.getRanAt().toString(), "ran_by", String.valueOf(run.getRanBy()),
                        "verdict", run.getVerdict(), "details", run.getDetailsJson())))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    private String actorRole(Authentication auth) {
        return auth != null
                ? auth.getAuthorities().stream().findFirst().map(Object::toString)
                        .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                        .orElse(null)
                : null;
    }
}
