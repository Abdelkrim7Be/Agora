package com.agora.gateway.agent;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;

/**
 * Per-instance role grant: a platform user holds a named role for one agent instance.
 * Roles: owner (configure + approve), approver (review/send only), viewer (inspect).
 * The global JWT role still applies for platform-level endpoints; this table governs
 * instance workspace access.
 */
@Entity
@Table(
    name = "agent_instance_grant",
    uniqueConstraints = @UniqueConstraint(columnNames = {"agent_instance_id", "user_id"})
)
public class AgentInstanceGrant {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "agent_instance_id", nullable = false)
    private String agentInstanceId;

    @Column(name = "user_id", nullable = false)
    private String userId;

    /** owner, approver, or viewer */
    @Column(nullable = false)
    private String role;

    @Column(nullable = false)
    private String grantedBy;

    private Instant grantedAt;

    private Instant expiresAt;

    public AgentInstanceGrant() {}

    public AgentInstanceGrant(String agentInstanceId, String userId, String role, String grantedBy) {
        this.agentInstanceId = agentInstanceId;
        this.userId = userId;
        this.role = role;
        this.grantedBy = grantedBy;
    }

    @PrePersist
    void prePersist() {
        if (grantedAt == null) grantedAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getAgentInstanceId() { return agentInstanceId; }
    public String getUserId() { return userId; }
    public String getRole() { return role; }
    public void setRole(String role) { this.role = role; }
    public String getGrantedBy() { return grantedBy; }
    public Instant getGrantedAt() { return grantedAt; }
    public Instant getExpiresAt() { return expiresAt; }
    public void setExpiresAt(Instant expiresAt) { this.expiresAt = expiresAt; }
}
