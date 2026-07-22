package com.agora.gateway.auth;

import org.springframework.data.repository.Repository;

public interface RevokedAccessTokenRepository extends Repository<RevokedAccessToken, String> {
    RevokedAccessToken save(RevokedAccessToken token);
    boolean existsById(String tokenId);
    long deleteByExpiresAtBefore(java.time.Instant cutoff);
}
