package com.agora.gateway.audit;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "audit_event")
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

    public AuditEvent() {}

    public AuditEvent(String username, String role, String action, String method,
                      String path, Integer upstreamStatus, String outcome) {
        this.timestamp = Instant.now();
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
}
