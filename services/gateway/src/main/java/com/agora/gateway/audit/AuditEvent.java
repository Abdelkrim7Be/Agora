package com.agora.gateway.audit;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(
        name = "audit_event",
        indexes = {
                @Index(
                        name = "idx_audit_login_account_window",
                        columnList = "username,action,outcome,timestamp"
                ),
                @Index(
                        name = "idx_audit_login_global_window",
                        columnList = "action,outcome,timestamp"
                )
        }
)
public class AuditEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Instant timestamp;

    private String username;
    private String role;

    @Column(nullable = false)
    private String action;

    @Column(nullable = false)
    private String method;

    @Column(nullable = false)
    private String path;

    private Integer upstreamStatus;

    @Column(nullable = false)
    private String outcome;

    // Hash chain: prevHash links to the previous row's hash (genesis constant for the
    // first row); hash covers prevHash + this row's own fields. Nullable only because
    // rows written before this chain existed have neither — verifyChain() treats those
    // as an unverifiable prefix rather than a break.
    private String prevHash;
    private String hash;

    public AuditEvent() {}

    public AuditEvent(String username, String role, String action, String method,
                      String path, Integer upstreamStatus, String outcome) {
        // Truncated to millisecond precision before it ever reaches JPA: the hash chain
        // recomputes this value after a DB round-trip, and some column types silently
        // drop sub-millisecond precision — truncating up front keeps write-time and
        // verify-time hashing byte-identical regardless of the underlying column type.
        this.timestamp = Instant.ofEpochMilli(Instant.now().toEpochMilli());
        this.username = username;
        this.role = role;
        this.action = action;
        this.method = method;
        this.path = path;
        this.upstreamStatus = upstreamStatus;
        this.outcome = outcome;
    }

    public Long getId() { return id; }
    public Instant getTimestamp() { return timestamp; }
    public String getUsername() { return username; }
    public String getRole() { return role; }
    public String getAction() { return action; }
    public String getMethod() { return method; }
    public String getPath() { return path; }
    public Integer getUpstreamStatus() { return upstreamStatus; }
    public String getOutcome() { return outcome; }
    /**
     * The only way a recorded field ever changes, and only for right-to-erasure:
     * the actor's name becomes a stable pseudonym so the row still links to the
     * same account without naming a person. Named rather than a plain setter so
     * the exception stays visible at every call site. The chain must be resealed
     * afterwards — see {@link AuditService#anonymizeActor}.
     */
    void anonymizeUsername(String pseudonym) { this.username = pseudonym; }

    public String getPrevHash() { return prevHash; }
    public void setPrevHash(String prevHash) { this.prevHash = prevHash; }
    public String getHash() { return hash; }
    public void setHash(String hash) { this.hash = hash; }
}
