package com.agora.gateway.auth;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;

import java.util.List;
import java.util.Optional;

public interface RefreshTokenRepository extends Repository<RefreshToken, String> {

    RefreshToken save(RefreshToken token);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select token from RefreshToken token where token.tokenHash = :tokenHash")
    Optional<RefreshToken> lockByTokenHash(String tokenHash);

    List<RefreshToken> findAllByFamilyId(String familyId);

    long deleteByExpiresAtBefore(java.time.Instant cutoff);
}
