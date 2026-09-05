ALTER TABLE forge.assistant_conversations ADD COLUMN body_purged_at timestamptz;
ALTER TABLE forge.assistant_conversations ADD COLUMN retention_checked_at timestamptz;
ALTER TABLE forge.assistant_turns ADD COLUMN body_purged_at timestamptz;
ALTER TABLE forge.assistant_proposals ADD COLUMN body_purged_at timestamptz;
CREATE INDEX assistant_retention_turns ON forge.assistant_turns
 (organization_id,owner_id,conversation_id,expires_at,id) WHERE body_purged_at IS NULL;
CREATE INDEX assistant_retention_proposals ON forge.assistant_proposals
 (organization_id,owner_id,conversation_id,turn_id,id) WHERE body_purged_at IS NULL;
CREATE INDEX assistant_retention_conversations ON forge.assistant_conversations
 (retention_checked_at NULLS FIRST,created_at,id) WHERE body_purged_at IS NULL;

-- Discovery returns identifiers only, never prompts, credentials or business data.
-- The security-definer boundary mirrors the existing import/Outbox worker discovery;
-- every read of bodies and every erasure still uses forge_app + organization/owner RLS.
-- The bound cannot be raised by callers, and progress ordering avoids one large
-- expired conversation monopolizing every polling batch.
CREATE FUNCTION forge.pending_assistant_retention(
 requested_limit integer DEFAULT 20, requested_organization uuid DEFAULT NULL
) RETURNS TABLE(organization_id uuid,owner_id uuid,conversation_id uuid)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT c.organization_id,c.owner_id,c.id
 FROM forge.assistant_conversations c
 WHERE c.body_purged_at IS NULL
  AND (requested_organization IS NULL OR c.organization_id=requested_organization)
  AND (c.deleted_at IS NOT NULL OR c.expires_at<=clock_timestamp()
   OR EXISTS(SELECT 1 FROM forge.assistant_turns t
     WHERE t.organization_id=c.organization_id AND t.owner_id=c.owner_id
      AND t.conversation_id=c.id AND t.body_purged_at IS NULL
      AND t.expires_at<=clock_timestamp())
   OR EXISTS(SELECT 1 FROM forge.assistant_proposals p JOIN forge.assistant_turns t
      ON t.organization_id=p.organization_id AND t.owner_id=p.owner_id
       AND t.conversation_id=p.conversation_id AND t.id=p.turn_id
     WHERE p.organization_id=c.organization_id AND p.owner_id=c.owner_id
      AND p.conversation_id=c.id AND p.body_purged_at IS NULL
      AND t.expires_at<=clock_timestamp()))
 ORDER BY c.retention_checked_at NULLS FIRST,c.created_at,c.id
 LIMIT greatest(least(coalesce(requested_limit,20),100),0)
 $$;
REVOKE ALL ON FUNCTION forge.pending_assistant_retention(integer,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forge.pending_assistant_retention(integer,uuid) TO forge_app;
