package com.agora.gateway.report;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * A problem reported by any signed-in user, for an admin to triage.
 *
 * Unlike {@code AuditEvent} this is deliberately mutable — status moves
 * open -> acknowledged -> resolved as an admin works through it, and the AI
 * suggestion is filled in asynchronously after the row already exists.
 */
@Entity
@Table(
        name = "platform_report",
        indexes = {
                @Index(name = "idx_platform_report_status", columnList = "status,createdAt")
        }
)
public class PlatformReport {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String reporterUsername;

    private String reporterRole;

    @Column(nullable = false)
    private String subject;

    @Column(nullable = false, length = 4000)
    private String description;

    /** User's own pick: low / medium / high. */
    @Column(nullable = false)
    private String severity;

    /** Where they were when they hit "Signaler" — an instance id, or null from a platform page. */
    private String contextInstanceId;

    /** The route they were on (e.g. "/instance/xyz/inbox"), free text for triage context. */
    private String contextPage;

    @Column(nullable = false)
    private String status = "open";

    @Column(nullable = false)
    private Instant createdAt;

    private Instant updatedAt;

    /** Filled in asynchronously by OllamaSuggestionService; null until it lands or if the model is unreachable. */
    @Column(length = 2000)
    private String aiSuggestion;

    /** The model's own severity read, independent of what the reporter picked. */
    private String aiSeverity;

    private String resolvedBy;
    private Instant resolvedAt;

    public PlatformReport() {}

    public PlatformReport(String reporterUsername, String reporterRole, String subject,
                          String description, String severity, String contextInstanceId, String contextPage) {
        this.reporterUsername = reporterUsername;
        this.reporterRole = reporterRole;
        this.subject = subject;
        this.description = description;
        this.severity = severity;
        this.contextInstanceId = contextInstanceId;
        this.contextPage = contextPage;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }

    public String getReporterUsername() { return reporterUsername; }
    public String getReporterRole() { return reporterRole; }
    public String getSubject() { return subject; }
    public String getDescription() { return description; }
    public String getSeverity() { return severity; }
    public String getContextInstanceId() { return contextInstanceId; }
    public String getContextPage() { return contextPage; }

    public String getStatus() { return status; }
    public void setStatus(String status) {
        this.status = status;
        this.updatedAt = Instant.now();
    }

    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public String getAiSuggestion() { return aiSuggestion; }
    public void setAiSuggestion(String aiSuggestion) { this.aiSuggestion = aiSuggestion; }

    public String getAiSeverity() { return aiSeverity; }
    public void setAiSeverity(String aiSeverity) { this.aiSeverity = aiSeverity; }

    public String getResolvedBy() { return resolvedBy; }
    public String getResolvedAt() { return resolvedAt == null ? null : resolvedAt.toString(); }

    public void markResolved(String by) {
        this.status = "resolved";
        this.resolvedBy = by;
        this.resolvedAt = Instant.now();
        this.updatedAt = this.resolvedAt;
    }
}
