package com.agora.gateway.agent;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "agent_instance")
public class AgentInstance {

    @Id
    private String id;

    @Column(nullable = false)
    private String agentType;

    @Column(nullable = false)
    private String displayName;

    private String mailboxIdentity;

    @Column(length = 2048)
    private String description;

    @Column(nullable = false)
    private String status = "active";

    @Column(nullable = false)
    private String basePath;

    @Column(nullable = false)
    private String allowedRoles = "owner";

    @Column(nullable = false)
    private String createdBy;

    private String color;
    private String icon;
    private Instant createdAt;

    public AgentInstance() {}

    public AgentInstance(String id, String agentType, String displayName, String mailboxIdentity,
                         String description, String status, String basePath, String allowedRoles,
                         String createdBy, String color, String icon) {
        this.id = id;
        this.agentType = agentType;
        this.displayName = displayName;
        this.mailboxIdentity = mailboxIdentity;
        this.description = description;
        this.status = status;
        this.basePath = basePath;
        this.allowedRoles = allowedRoles;
        this.createdBy = createdBy;
        this.color = color;
        this.icon = icon;
    }

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = Instant.now();
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getAgentType() { return agentType; }
    public void setAgentType(String agentType) { this.agentType = agentType; }

    public String getDisplayName() { return displayName; }
    public void setDisplayName(String displayName) { this.displayName = displayName; }

    public String getMailboxIdentity() { return mailboxIdentity; }
    public void setMailboxIdentity(String mailboxIdentity) { this.mailboxIdentity = mailboxIdentity; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getBasePath() { return basePath; }
    public void setBasePath(String basePath) { this.basePath = basePath; }

    public String getAllowedRoles() { return allowedRoles; }
    public void setAllowedRoles(String allowedRoles) { this.allowedRoles = allowedRoles; }

    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }

    public String getColor() { return color; }
    public void setColor(String color) { this.color = color; }

    public String getIcon() { return icon; }
    public void setIcon(String icon) { this.icon = icon; }

    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
}
