package com.agora.gateway.agent;

import org.springframework.data.repository.Repository;

import java.util.List;
import java.util.Optional;

public interface AgentInstanceGrantRepository extends Repository<AgentInstanceGrant, Long> {

    AgentInstanceGrant save(AgentInstanceGrant grant);

    List<AgentInstanceGrant> findByAgentInstanceId(String agentInstanceId);

    List<AgentInstanceGrant> findByUserIdIgnoreCase(String userId);

    Optional<AgentInstanceGrant> findByAgentInstanceIdAndUserId(String agentInstanceId, String userId);

    void deleteById(Long id);

    boolean existsByAgentInstanceIdAndUserId(String agentInstanceId, String userId);
}
