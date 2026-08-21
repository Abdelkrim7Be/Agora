package com.agora.gateway.report;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * "Signaler un problème": any signed-in user can file a report; only an admin
 * can see and triage the inbox. Route authorization (ROLE_*) is enforced in
 * SecurityConfig — this controller assumes the caller already cleared that gate.
 */
@RestController
@RequestMapping("/reports")
public class ReportController {

    private static final Set<String> VALID_SEVERITIES = Set.of("low", "medium", "high");
    private static final Set<String> VALID_STATUSES = Set.of("open", "acknowledged", "resolved");

    private final PlatformReportRepository reports;
    private final UserRepository users;
    private final ReportMailer mailer;
    private final OllamaSuggestionService suggestionService;
    private final AuditService auditService;

    public ReportController(PlatformReportRepository reports, UserRepository users, ReportMailer mailer,
                            OllamaSuggestionService suggestionService, AuditService auditService) {
        this.reports = reports;
        this.users = users;
        this.mailer = mailer;
        this.suggestionService = suggestionService;
        this.auditService = auditService;
    }

    public record CreateReportRequest(
            @NotBlank String subject,
            @NotBlank String description,
            String severity,
            String contextInstanceId,
            String contextPage
    ) {}

    public record UpdateReportRequest(@NotBlank String status) {}

    public record ReportResponse(
            Long id, String reporterUsername, String reporterRole, String subject, String description,
            String severity, String contextInstanceId, String contextPage, String status,
            Instant createdAt, Instant updatedAt, String aiSuggestion, String aiSeverity,
            String resolvedBy, String resolvedAt
    ) {
        static ReportResponse from(PlatformReport r) {
            return new ReportResponse(r.getId(), r.getReporterUsername(), r.getReporterRole(), r.getSubject(),
                    r.getDescription(), r.getSeverity(), r.getContextInstanceId(), r.getContextPage(),
                    r.getStatus(), r.getCreatedAt(), r.getUpdatedAt(), r.getAiSuggestion(), r.getAiSeverity(),
                    r.getResolvedBy(), r.getResolvedAt());
        }
    }

    /** Any authenticated role — SecurityConfig gates this to "authenticated", not a specific role. */
    @PostMapping
    public ResponseEntity<?> create(@Valid @RequestBody CreateReportRequest req, Authentication auth) {
        String severity = req.severity() == null || req.severity().isBlank() ? "medium" : req.severity().toLowerCase();
        if (!VALID_SEVERITIES.contains(severity)) {
            return ResponseEntity.badRequest().body(Map.of("error", "invalid severity: " + req.severity()));
        }
        PlatformReport report = new PlatformReport(
                actor(auth), actorRole(auth), req.subject(), req.description(), severity,
                blankToNull(req.contextInstanceId()), blankToNull(req.contextPage())
        );
        PlatformReport saved = reports.save(report);
        audit(auth, "report_submit", "success");

        suggestionService.suggestAsync(saved.getId());

        List<String> adminEmails = users.findByRoleIgnoreCaseAndEnabledTrue("admin").stream()
                .map(AppUser::getEmail).filter(email -> email != null && !email.isBlank()).toList();
        mailer.notifyAdmins(adminEmails, saved);

        return ResponseEntity.status(HttpStatus.CREATED).body(ReportResponse.from(saved));
    }

    @GetMapping
    public List<ReportResponse> list(@RequestParam(required = false) String status) {
        List<PlatformReport> rows = status == null || status.isBlank()
                ? reports.findAllByOrderByCreatedAtDesc()
                : reports.findByStatusOrderByCreatedAtDesc(status);
        return rows.stream().map(ReportResponse::from).toList();
    }

    @PutMapping("/{id}")
    public ResponseEntity<?> update(@PathVariable Long id, @Valid @RequestBody UpdateReportRequest req, Authentication auth) {
        String status = req.status().toLowerCase();
        if (!VALID_STATUSES.contains(status)) {
            return ResponseEntity.badRequest().body(Map.of("error", "invalid status: " + req.status()));
        }
        return reports.findById(id).map(report -> {
            if ("resolved".equals(status)) {
                report.markResolved(auth != null ? auth.getName() : null);
            } else {
                report.setStatus(status);
            }
            PlatformReport saved = reports.save(report);
            audit(auth, "report_update_status", "success");
            return ResponseEntity.ok((Object) ReportResponse.from(saved));
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private String actor(Authentication auth) {
        return auth != null ? auth.getName() : "unknown";
    }

    private String actorRole(Authentication auth) {
        return auth != null
                ? auth.getAuthorities().stream().findFirst().map(Object::toString)
                        .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                        .orElse(null)
                : null;
    }

    private void audit(Authentication auth, String action, String outcome) {
        auditService.record(actor(auth), actorRole(auth), action, "POST", "/reports", null, outcome);
    }
}
