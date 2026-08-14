package com.agora.gateway.report;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface PlatformReportRepository extends JpaRepository<PlatformReport, Long> {
    List<PlatformReport> findAllByOrderByCreatedAtDesc();
    List<PlatformReport> findByStatusOrderByCreatedAtDesc(String status);
}
