package com.agora.gateway.health;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * One "Auditer la plateforme" click: an aggregate verdict across every agent
 * instance plus the gateway's own checks, persisted so an admin can see
 * whether today's warning was also there yesterday.
 */
@Entity
@Table(name = "platform_audit_run")
public class PlatformAuditRun {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Instant ranAt;

    private String ranBy;

    /** ok / warn / critical */
    @Column(nullable = false)
    private String verdict;

    @Column(nullable = false)
    private int instanceCount;

    @Column(nullable = false)
    private int warningCount;

    /** Serialized list of per-instance breakdowns, JSON. Kept as one blob rather
     *  than a child table — this is a diagnostic snapshot, not queried by field. */
    @Column(length = 8000)
    private String detailsJson;

    public PlatformAuditRun() {}

    public PlatformAuditRun(String ranBy, String verdict, int instanceCount, int warningCount, String detailsJson) {
        this.ranAt = Instant.now();
        this.ranBy = ranBy;
        this.verdict = verdict;
        this.instanceCount = instanceCount;
        this.warningCount = warningCount;
        this.detailsJson = detailsJson;
    }

    public Long getId() { return id; }
    public Instant getRanAt() { return ranAt; }
    public String getRanBy() { return ranBy; }
    public String getVerdict() { return verdict; }
    public int getInstanceCount() { return instanceCount; }
    public int getWarningCount() { return warningCount; }
    public String getDetailsJson() { return detailsJson; }
}
