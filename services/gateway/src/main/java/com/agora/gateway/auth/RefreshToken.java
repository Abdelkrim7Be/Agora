package com.agora.gateway.auth;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;

@Entity
@Table(
        name = "refresh_token",
        indexes = {
                @Index(name = "idx_refresh_token_family", columnList = "family_id"),
                @Index(name = "idx_refresh_token_expiry", columnList = "expires_at")
        }
)
public class RefreshToken {

    @Id
    @Column(length = 64, nullable = false, updatable = false)
    private String tokenHash;

    @Column(name = "family_id", length = 36, nullable = false, updatable = false)
    private String familyId;

    @Column(nullable = false, updatable = false)
    private String username;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "expires_at", nullable = false, updatable = false)
    private Instant expiresAt;

    @Column(name = "used_at")
    private Instant usedAt;
    @Column(name = "revoked_at")
    private Instant revokedAt;

    @Column(name = "replacement_hash", length = 64)
    private String replacementHash;

    @Version
    private long version;

    protected RefreshToken() {}

    RefreshToken(String tokenHash, String familyId, String username,
                 Instant createdAt, Instant expiresAt) {
        this.tokenHash = tokenHash;
        this.familyId = familyId;
        this.username = username;
        this.createdAt = createdAt;
        this.expiresAt = expiresAt;
    }

    public String getTokenHash() { return tokenHash; }
    public String getFamilyId() { return familyId; }
    public String getUsername() { return username; }
    public Instant getExpiresAt() { return expiresAt; }
    public Instant getUsedAt() { return usedAt; }
    public Instant getRevokedAt() { return revokedAt; }

    void markUsed(Instant now, String replacementHash) {
        this.usedAt = now;
        this.replacementHash = replacementHash;
    }

    void revoke(Instant now) {
        if (revokedAt == null) {
            revokedAt = now;
        }
    }
}
