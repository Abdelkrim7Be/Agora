package com.agora.gateway.user;

import org.springframework.data.repository.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Narrowed on purpose, the same way {@code AuditRepository} is: only save and the
 * reads the invitation flow needs. Extending JpaRepository would expose delete
 * and bulk-update over a table that records who was invited and when.
 */
public interface UserInvitationRepository extends Repository<UserInvitation, Long> {

    UserInvitation save(UserInvitation invitation);

    Optional<UserInvitation> findByTokenHash(String tokenHash);

    List<UserInvitation> findByUserIdAndConsumedAtIsNull(Long userId);
}
