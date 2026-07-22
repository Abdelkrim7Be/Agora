package com.agora.gateway.auth;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(
        name = "revoked_access_token",
        indexes = @Index(name = "idx_revoked_access_expiry", columnList = "expires_at")
)
public class RevokedAccessToken {

    @Id
    @Column(length = 36, nullable = false, updatable = false)
    private String tokenId;

    @Column(name = "expires_at", nullable = false, updatable = false)
    private Instant expiresAt;

    protected RevokedAccessToken() {}

    RevokedAccessToken(String tokenId, Instant expiresAt) {
        this.tokenId = tokenId;
        this.expiresAt = expiresAt;
    }
}
