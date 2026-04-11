# 03 — Class Diagrams

---

## 1. Core Abstractions (ABC hierarchy)

```mermaid
classDiagram
    class GraphStore {
        <<abstract>>
        +create_node(node: BaseNode) dict
        +get_node(node_id: str) dict|None
        +update_node(node_id: str, props: dict) dict
        +delete_node(node_id: str) bool
        +list_nodes(label: str) list[dict]
        +create_edge(src, tgt, edge_type, props) dict
        +get_edges(node_id, direction, edge_type) list[dict]
        +vector_search(embedding, label, k) list[dict]
    }
    class AgeGraphStore {
        -_pool: AsyncConnectionPool
        -_graph: str
        +execute_cypher(query) list
    }
    GraphStore <|-- AgeGraphStore

    class GraphQueryEngine {
        <<abstract>>
        +critical_path(goal_id: str) list[dict]
        +dependencies(task_id: str, depth: int) list[dict]
        +blocked_tasks(user_id: str) list[dict]
    }
    class AgeGraphQueryEngine {
        -_store: AgeGraphStore
    }
    GraphQueryEngine <|-- AgeGraphQueryEngine

    class StorageClient {
        <<abstract>>
        +read(path: str) bytes
        +write(path, data, content_type) None
        +delete(path: str) None
        +list_objects(prefix: str) list[str]
        +exists(path: str) bool
    }
    class S3StorageClient {
        -_bucket: str
        -_endpoint_url: str|None
        -_region: str
        -_client: object
        -_get_client() object
    }
    StorageClient <|-- S3StorageClient

    class SecretsClient {
        <<abstract>>
        +get_secret(key: str) str
        +set_secret(key, value) None
        +delete_secret(key: str) None
        +list_secrets() list[str]
    }
    class EnvFileSecretsClient {
        -_path: str
    }
    class AWSSecretsClient {
        -_prefix: str
        -_region: str
    }
    class HashiCorpVaultClient {
        -_url: str
        -_mount: str
        -_token: str
    }
    SecretsClient <|-- EnvFileSecretsClient
    SecretsClient <|-- AWSSecretsClient
    SecretsClient <|-- HashiCorpVaultClient

    class LLMClient {
        <<abstract>>
        +complete(messages, model, tools) LLMResponse
        +stream(messages, model) AsyncIterator
        +embed(texts) list[list[float]]
    }
    class AnthropicLLMClient {
        -_client: Anthropic
        -_default_model: str
    }
    class OpenAILLMClient {
        -_client: OpenAI
        -_default_model: str
    }
    class LiteLLMLLMClient {
        -_default_model: str
    }
    LLMClient <|-- AnthropicLLMClient
    LLMClient <|-- OpenAILLMClient
    LLMClient <|-- LiteLLMLLMClient
```

---

## 2. Domain Node Hierarchy

```mermaid
classDiagram
    class BaseNode {
        +id: str
        +label: str
        +created_at: datetime
        +updated_at: datetime
        +version: int
    }

    class TaskNode {
        +task_type: TaskType
        +title: str
        +description: str
        +state: TaskState
        +state_history: list[StateHistoryEntry]
        +timeline: Timeline
        +scoring: ScoringBlock
        +progress: ProgressBlock
        +override: OverrideBlock
        +autonomy: AutonomyBlock
        +on_critical_path: bool
        +tags: list[str]
    }

    class GoalNode {
        +title: str
        +description: str
        +owner_id: str
        +state: GoalState
        +priority: GoalPriority
        +origin: GoalOrigin
        +timeline: GoalTimeline
        +progress: GoalProgress
        +success_criteria: list[str]
    }

    class UserNode {
        +email: str
        +name: str
        +role: str
        +preferences: UserPreferences
        +behavioral_model: BehavioralModel
        +working_hours: WorkingHours
    }

    class ResourceNode {
        +name: str
        +resource_type: ResourceType
        +capacity: CapacityModel
        +reliability: ReliabilityModel
        +availability_status: AvailabilityStatus
        +current_risk: CurrentRisk
    }

    class ConstraintNode {
        +title: str
        +constraint_type: ConstraintType
        +scope: ConstraintScope
        +threshold: float
        +current_value: float
        +risk_level: RiskLevel
    }

    class OrganizationNode {
        +name: str
        +domain: str|None
        +owner_id: str
        +members: list[OrgMember]
        +settings: OrgSettings
    }

    class WorkspaceNode {
        +name: str
        +org_id: str
        +visibility: WorkspaceVisibility
        +owner_id: str
    }

    class MCPServerNode {
        +name: str
        +description: str
        +command: str
        +transport: MCPTransport
        +trust_tier: TrustTier
        +enabled: bool
        +user_id: str
    }

    class CheckinNode {
        +task_id: str
        +resource_id: str
        +state: CheckinState
        +message: str
        +response: str|None
        +scheduled_at: datetime
    }

    class VisibilityGrantNode {
        +grantor_id: str
        +grantee_id: str
        +scope: VisibilityScope
        +expires_at: datetime|None
    }

    BaseNode <|-- TaskNode
    BaseNode <|-- GoalNode
    BaseNode <|-- UserNode
    BaseNode <|-- ResourceNode
    BaseNode <|-- ConstraintNode
    BaseNode <|-- OrganizationNode
    BaseNode <|-- WorkspaceNode
    BaseNode <|-- MCPServerNode
    BaseNode <|-- CheckinNode
    BaseNode <|-- VisibilityGrantNode
```

---

## 3. Auth & Security Classes

```mermaid
classDiagram
    class JWTService {
        -_private_key: str
        -_public_key: str
        -_algorithm: str
        -_redis: Redis|None
        +from_env()$ JWTService
        +issue_access_token(user_id, role) str
        +issue_refresh_token(user_id) str
        +verify_token(token) dict
        +verify_token_async(token) dict
        +revoke_token(token) None
        -_issue_token(user_id, type, expire_s, role) str
        -_is_revoked(jti) bool
    }

    class OAuthService {
        -_providers: dict[str, OAuthProvider]
        -_state_store: dict
        +from_env()$ OAuthService
        +get_authorization_url(provider_name, redirect_uri) tuple
        +exchange_code(provider_name, code, state, redirect_uri) dict
        -_store_state(state, value) None
        -_verify_state(state) str
    }

    class OAuthProvider {
        +name: str
        +client_id: str
        +client_secret: str
        +authorize_url: str
        +token_url: str
        +userinfo_url: str
        +scopes: list[str]
    }

    class JWTRoleMiddleware {
        +dispatch(request, call_next) Response
    }

    class UserProvisioningService {
        -_store: GraphStore
        +provision_new_user(provider, provider_user_id, email, name) ProvisioningResult
        +get_or_create_user(user_id) UserNode
    }

    OAuthService "1" *-- "1..*" OAuthProvider
    JWTService ..> JWTRoleMiddleware : decoded by
```

---

## 4. Scoring Engine & Factors

```mermaid
classDiagram
    class ScoringEngine {
        -_weights: ScoringWeights
        +score_task(task, context) ScoringBlock
        +score_batch(task_ids, graph_store) list[ScoringBlock]
        +simulate(node_ids, overrides) list[dict]
        +update_weights(weights) None
    }

    class ScoringWeights {
        +w1_timeline_urgency: float
        +w2_dependency_weight: float
        +w3_critical_path: float
        +w4_blocker: float
        +w5_human_override: float
        +w6_resource_risk: float
        +w7_constraint_pressure: float
    }

    class ScoringBlock {
        +timeline_urgency: float
        +dependency_weight: float
        +critical_path: float
        +blocker: float
        +human_override: float
        +resource_risk: float
        +constraint_pressure: float
        +computed_priority: float
        +chain_urgency_rollup: float
        +last_scored_at: datetime
        +score_reasoning: str
    }

    ScoringEngine "1" *-- "1" ScoringWeights
    ScoringEngine ..> ScoringBlock : produces
    ScoringEngine ..> W1 : uses
    ScoringEngine ..> W2 : uses
    ScoringEngine ..> W3 : uses
    ScoringEngine ..> W4 : uses
    ScoringEngine ..> W5 : uses
    ScoringEngine ..> W6 : uses
    ScoringEngine ..> W7 : uses

    class W1["timeline_urgency.py\nW1: Timeline Urgency"] { }
    class W2["dependency_weight.py\nW2: Dependency Weight"] { }
    class W3["critical_path.py\nW3: Critical Path (0 or 1)"] { }
    class W4["blocker.py\nW4: Blocker (0 / 0.6 / 1.0)"] { }
    class W5["override.py\nW5: Human Override (−0.3 to +1.0)"] { }
    class W6["resource_risk.py\nW6: Resource Risk"] { }
    class W7["constraint.py\nW7: Constraint Pressure"] { }
```

---

## 5. Channel Adapter Hierarchy

```mermaid
classDiagram
    class ChannelAdapter {
        <<abstract>>
        +channel_name: str
        +normalize(raw_payload) InboundMessage
        +verify_signature(request) bool
    }

    class EmailChannelAdapter {
        -_config: EmailConfig
        -_poller: EmailPoller
        +normalize(raw) InboundMessage
    }
    class EmailPoller {
        -_host: str
        -_port: int
        -_username: str
        -_password: str
        +poll() AsyncIterator[dict]
    }
    class EmailSender {
        -_host: str
        -_port: int
        +send(message: OutboundMessage) None
    }

    class SlackAdapter {
        -_config: SlackConfig
        +normalize(raw) InboundMessage
        +verify_signature(request) bool
    }
    class SlackSender {
        -_bot_token: str
        +send(message: OutboundMessage) None
    }

    class TeamsAdapter {
        -_config: TeamsConfig
        +normalize(raw) InboundMessage
    }
    class TeamsSender {
        -_webhook_url: str
        +send(message: OutboundMessage) None
    }

    class TelegramChannelAdapter {
        -_bot_token: str
        +normalize(raw) InboundMessage
    }
    class TelegramSender {
        +send(message: OutboundMessage) None
    }

    class WhatsAppChannelAdapter {
        -_config: dict
        +normalize(raw) InboundMessage
    }
    class WhatsAppSender {
        +send(message: OutboundMessage) None
    }

    ChannelAdapter <|-- EmailChannelAdapter
    ChannelAdapter <|-- SlackAdapter
    ChannelAdapter <|-- TeamsAdapter
    ChannelAdapter <|-- TelegramChannelAdapter
    ChannelAdapter <|-- WhatsAppChannelAdapter

    EmailChannelAdapter "1" *-- "1" EmailPoller
    EmailChannelAdapter "1" *-- "1" EmailSender
    SlackAdapter "1" *-- "1" SlackSender
    TeamsAdapter "1" *-- "1" TeamsSender
    TelegramChannelAdapter "1" *-- "1" TelegramSender
    WhatsAppChannelAdapter "1" *-- "1" WhatsAppSender
```

---

## 6. Skill & MCP Execution Classes

```mermaid
classDiagram
    class SkillRegistryService {
        -_store: GraphStore
        -_storage: StorageClient
        +register(skill: Skill) None
        +get(skill_id: str) Skill|None
        +search(query: str) list[Skill]
        +list_sources() list[SkillSource]
        +list_workers() list[WorkerStatus]
    }

    class SkillWorker {
        -_llm: LLMClient
        -_store: GraphStore
        +execute(skill_id, task_id, context) SkillResult
        +get_status() WorkerStatus
    }

    class Skill {
        +skill_id: str
        +name: str
        +description: str
        +version: str
        +system_prompt: str
        +tools: list[str]
        +model: str
    }

    class MCPRegistry {
        -_store: GraphStore
        +register(user_id, node: MCPServerNode) None
        +get(server_id: str) MCPServerNode|None
        +list_for_user(user_id, enabled_only) list[MCPServerNode]
        +update_trust(server_id, trust_tier) MCPServerNode
    }

    class MCPClient {
        +call_tool(server_id, tool_name, args) dict
        +list_tools(server_id) list[dict]
    }

    class GatedApprovalService {
        -_store: GraphStore
        -_broker: MessageBroker
        +request_approval(user_id, server_id, tool_name, args) str
        +approve(approval_id) None
        +reject(approval_id) None
        +list_pending(user_id) list[dict]
    }

    class OfficialMCPRegistry {
        +search(query: str) list[dict]
        +get_by_id(server_id: str) dict
        +list_all() list[dict]
    }

    SkillRegistryService "1" *-- "0..*" Skill
    SkillWorker --> LLMClient : uses
    SkillWorker --> SkillRegistryService : resolves skill
    MCPRegistry "1" ..> "0..*" MCPServerNode : manages
    MCPClient --> MCPRegistry : looks up server
    GatedApprovalService --> MCPClient : wraps
```
