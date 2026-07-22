package com.agora.gateway.audit;

import org.springframework.data.domain.Pageable;
import org.springframework.data.repository.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

// Deliberately extends the minimal Repository (not JpaRepository) so the audit log
// exposes only append + read — no delete/update surface. Keeps the trail tamper-proof.
public interface AuditRepository extends Repository<AuditEvent, Long> {
    AuditEvent save(AuditEvent event);

    List<AuditEvent> findAll();

    List<AuditEvent> findAllByOrderByTimestampDesc(Pageable pageable);

    // Insertion order (id), not timestamp: two events can share a millisecond and
    // the hash chain must walk in the exact order rows were linked and saved.
    List<AuditEvent> findAllByOrderByIdAsc();

    Optional<AuditEvent> findTopByOrderByIdDesc();

    long countByUsernameIgnoreCaseAndActionAndOutcomeAndTimestampAfter(
            String username,
            String action,
            String outcome,
            Instant cutoff
    );

    long countByActionAndOutcomeAndTimestampAfter(
            String action,
            String outcome,
            Instant cutoff
    );
}
