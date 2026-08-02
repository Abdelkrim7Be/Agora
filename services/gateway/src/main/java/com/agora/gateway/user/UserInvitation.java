package com.agora.gateway.user;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * A pending "set your password" invitation.
 *
 * Only the SHA-256 of the token is stored. The clear token exists once, in the
 * response to the admin that created the invitation and in the mail sent to the
 * invitee — a database read cannot recover it, so a dump of this table does not
 * let anyone take over an account.
 *
 * Consumption is the only mutation: everything else is set once at creation.
 */
@Entity
@Table(name = "user_invitation")
public class UserInvitation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long userId;

    /** Hex SHA-256 of the clear token. Indexed lookups match on this, never on the token. */
    @Column(nullable = false, unique = true, length = 64)
    private String tokenHash;

    @Column(nullable = false)
    private Instant expiresAt;

    private String createdBy;

    private Instant createdAt;

    /** Set exactly once, in the same transaction as the password write. */
    private Instant consumedAt;

    public UserInvitation() {}

    public UserInvitation(Long userId, String tokenHash, Instant expiresAt, String createdBy) {
        this.userId = userId;
        this.tokenHash = tokenHash;
        this.expiresAt = expiresAt;
        this.createdBy = createdBy;
    }

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = Instant.now();
    }

    public boolean isConsumed() { return consumedAt != null; }

    public boolean isExpired(Instant now) { return expiresAt == null || expiresAt.isBefore(now); }

    public boolean isUsable(Instant now) { return !isConsumed() && !isExpired(now); }

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getTokenHash() { return tokenHash; }
    public Instant getExpiresAt() { return expiresAt; }
    public String getCreatedBy() { return createdBy; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getConsumedAt() { return consumedAt; }
    public void setConsumedAt(Instant consumedAt) { this.consumedAt = consumedAt; }
}
