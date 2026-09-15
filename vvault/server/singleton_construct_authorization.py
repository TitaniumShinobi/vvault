"""OVVAULTS authorization for construct-authored singleton transcript turns."""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable

try:
    from . import chatty_body_service
    from .conversation_thread_service import (
        ConversationContractError,
        _verified_graduation_execution_authorization,
        sign_payload,
        verified_qa_evidence_envelope,
        verify_payload,
    )
except ImportError:
    import chatty_body_service
    from conversation_thread_service import (
        ConversationContractError,
        _verified_graduation_execution_authorization,
        sign_payload,
        verified_qa_evidence_envelope,
        verify_payload,
    )


PARTICIPANT_FRAME_CONTRACT = "chatty-participant-frame/v1"
ADDRESSING_CONTRACT = "chatty-addressing/v1"
SINGLETON_FRAME_CONTRACT = "chatty-singleton-construct-frame/v1"
CROSS_SURFACE_BINDING_CONTRACT = "chatty-cross-surface-role-binding/v1"
STAGE_PROFILE_AUTHORIZATION_CONTRACT = "chatty-graduation-stage-profile-authorization/v1"
SAME_PRINCIPAL_PARTICIPANT_MODE = "same_principal_cross_surface"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
PRINCIPAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SURFACE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _principal_id(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not PRINCIPAL_ID_RE.fullmatch(normalized):
        raise ConversationContractError("INVALID_FIELD", f"{field} is invalid")
    return normalized


def _address(principal_id: str) -> str:
    return re.sub(r"-\d+$", "", principal_id)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _trusted_principal_display_name(value: Any, principal_id: str) -> str:
    """Keep participant labels bounded; malformed canonical prose falls back to the principal."""
    raw = str(value or "").strip()
    fallback = chatty_body_service.display_name(principal_id)
    if not raw or CONTROL_RE.search(raw):
        return fallback
    normalized = re.sub(r"\s+", " ", raw).strip()
    if not normalized or len(normalized) > 120:
        return fallback
    return normalized


@dataclass
class SingletonConstructAuthorizationService:
    """Derive signed singleton participant frames from canonical owner data."""

    connect: Callable[[], Any] = chatty_body_service._connect
    signing_secret: str | None = None

    @staticmethod
    def _stage_profile_authorization(value: Any) -> dict[str, Any]:
        required = {
            "contract", "programId", "stageProfileId", "stageProfileHash",
            "stageProfileStateReceiptHash", "stageId", "stageCaseId",
            "participantMode",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ConversationContractError(
                "STAGE_PROFILE_AUTHORIZATION_REQUIRED",
                "Same-principal operation requires exact active stage-profile authorization",
                403,
            )
        normalized = dict(value)
        if (
            normalized.get("contract") != STAGE_PROFILE_AUTHORIZATION_CONTRACT
            or normalized.get("participantMode") != SAME_PRINCIPAL_PARTICIPANT_MODE
        ):
            raise ConversationContractError(
                "STAGE_PROFILE_AUTHORIZATION_INVALID",
                "Same-principal stage-profile authorization is invalid",
                403,
            )
        for field in ("programId", "stageProfileId", "stageId", "stageCaseId"):
            normalized[field] = _principal_id(normalized.get(field), field)
        for field in ("stageProfileHash", "stageProfileStateReceiptHash"):
            normalized[field] = str(normalized.get(field) or "").strip().lower()
            if not SHA256_RE.fullmatch(normalized[field]):
                raise ConversationContractError(
                    "STAGE_PROFILE_AUTHORIZATION_INVALID",
                    f"{field} is invalid",
                    403,
                )
        return normalized

    def _assert_active_stage_profile(
        self,
        cur: Any,
        owner_user_id: str,
        thread_id: str,
        authorization: dict[str, Any],
    ) -> None:
        """Resolve the active profile from VVAULT-signed append-only QA rows.

        Caller-supplied hashes are references only. They cannot activate a
        same-principal frame unless the owner-qualified canonical QA stream has
        the matching open profile and no matching completion event.
        """
        cur.execute(
            """SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                      evidence_sha256,signature,actor_principal_id,created_at
                 FROM ovvaults.qa_evaluation_events
                WHERE owner_user_id=%s AND qa_session_id=%s
                  AND thread_id=%s
                  AND event_type IN ('stage_profile_opened','stage_profile_completed')
                  AND evidence ? 'stageProfileEvent'
                  AND evidence->'stageProfileEvent'->>'contract'=
                      'chatty-graduation-stage-profile-event/v1'
                  AND evidence->'stageProfileEvent'->>'profileStreamId'=%s
                ORDER BY qa_event_sequence ASC NULLS LAST, created_at ASC, qa_event_id ASC""",
            (
                owner_user_id,
                authorization["programId"],
                thread_id,
                authorization["stageProfileId"],
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]
        if not rows:
            raise ConversationContractError(
                "STAGE_PROFILE_AUTHORIZATION_NOT_FOUND",
                "The active owner-qualified stage profile was not found",
                403,
            )
        verified_rows = [
            verified_qa_evidence_envelope(row, self.signing_secret or "")
            for row in rows
        ]
        stage_events = []
        for outer in verified_rows:
            outer_evidence = outer.get("evidence")
            stage_event = (
                outer_evidence.get("stageProfileEvent")
                if isinstance(outer_evidence, dict)
                else None
            )
            if (
                not isinstance(stage_event, dict)
                or stage_event.get("contract")
                != "chatty-graduation-stage-profile-event/v1"
                or stage_event.get("programId") != authorization["programId"]
                or stage_event.get("profileStreamId")
                != authorization["stageProfileId"]
                or stage_event.get("eventType") != outer.get("eventType")
            ):
                raise ConversationContractError(
                    "STAGE_PROFILE_EVIDENCE_INVALID",
                    "Canonical stage-profile outer and nested evidence disagree",
                    409,
                )
            stage_events.append(stage_event)
        if (
            stage_events[0].get("eventType") != "stage_profile_opened"
            or sum(
                event.get("eventType") == "stage_profile_opened"
                for event in stage_events
            )
            != 1
        ):
            raise ConversationContractError(
                "STAGE_PROFILE_EVIDENCE_INVALID",
                "Canonical stage-profile opening evidence is not unique",
                409,
            )
        if any(
            event.get("eventType") == "stage_profile_completed"
            for event in stage_events
        ):
            raise ConversationContractError(
                "STAGE_PROFILE_NOT_ACTIVE",
                "The cumulative graduation stage profile is not active",
                409,
            )
        latest_verified = verified_rows[-1]
        if latest_verified.get("eventType") != "stage_profile_opened":
            raise ConversationContractError(
                "STAGE_PROFILE_NOT_ACTIVE",
                "The cumulative graduation stage profile is not active",
                409,
        )
        evidence = latest_verified.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        event = (
            evidence.get("stageProfileEvent")
            if isinstance(evidence, dict)
            else {}
        )
        opening = event.get("payload") if isinstance(event, dict) else {}
        profile = opening.get("profile") if isinstance(opening, dict) else {}
        canonical_principal_id = thread_id.split("_chat_with_", 1)[0]
        role_policy = profile.get("rolePolicy") if isinstance(profile.get("rolePolicy"), dict) else {}
        expected_mention = f"@{_address(canonical_principal_id)}"
        if (
            event.get("eventType") != "stage_profile_opened"
            or event.get("programId") != authorization["programId"]
            or event.get("profileStreamId") != authorization["stageProfileId"]
            or opening.get("ownerPrincipalId") != str(owner_user_id)
            or profile.get("contract") != "chatty-graduation-stage-profile/v1"
            or profile.get("profileId") != authorization["stageProfileId"]
            or profile.get("profileHash") != authorization["stageProfileHash"]
            or profile.get("stageId") != authorization["stageId"]
            or profile.get("constructPrincipalId") != canonical_principal_id
            or profile.get("constructPrincipalId")
            != profile.get("evaluatorConstructPrincipalId")
            or role_policy.get("mode") != SAME_PRINCIPAL_PARTICIPANT_MODE
            or role_policy.get("canonicalPrincipalId")
            != profile.get("constructPrincipalId")
            or role_policy.get("originSurface") != "codex"
            or role_policy.get("respondentSurface") != "chatty"
            or role_policy.get("requestMention") != expected_mention
            or role_policy.get("responseMention") != expected_mention
            or role_policy.get("onBehalfOf") is not None
        ):
            raise ConversationContractError(
                "STAGE_PROFILE_AUTHORIZATION_SCOPE_INVALID",
                "Canonical stage-profile evidence does not match the requested scope",
                403,
            )

    def _construct_principal(
        self,
        cur: Any,
        owner_user_id: str,
        construct_id: str,
        *,
        surface: str,
        role: str,
    ) -> dict[str, Any]:
        cur.execute(
            """SELECT filename,storage_path,content,metadata
                 FROM ovvaults.vault_files
                WHERE user_id::text=%s AND lower(construct_id)=lower(%s)
                  AND (
                    lower(COALESCE(filename,''))='prompt.json'
                    OR lower(COALESCE(filename,'')) LIKE '%%/prompt.json'
                    OR lower(COALESCE(storage_path,'')) LIKE '%%/identity/prompt.json'
                    OR NULLIF(BTRIM(COALESCE(metadata->>'display_name','')), '') IS NOT NULL
                  )
                ORDER BY CASE
                  WHEN lower(COALESCE(filename,''))='prompt.json'
                    OR lower(COALESCE(filename,'')) LIKE '%%/prompt.json'
                    OR lower(COALESCE(storage_path,'')) LIKE '%%/identity/prompt.json'
                  THEN 0 ELSE 1 END,
                  created_at DESC
                LIMIT 8""",
            (str(owner_user_id), construct_id),
        )
        rows = [dict(row) for row in cur.fetchall()]
        if not rows:
            raise ConversationContractError(
                "PRINCIPAL_NOT_FOUND",
                "Construct principal is not resolved for the authenticated owner",
                404,
            )

        display_name = ""
        for row in rows:
            basename = str(row.get("filename") or row.get("storage_path") or "").rsplit("/", 1)[-1].lower()
            metadata = _json_object(row.get("metadata"))
            if basename == "prompt.json":
                prompt = _json_object(row.get("content"))
                display_name = str(
                    prompt.get("displayName")
                    or prompt.get("display_name")
                    or prompt.get("fullName")
                    or prompt.get("name")
                    or metadata.get("display_name")
                    or ""
                ).strip()
                if display_name:
                    break
            display_name = display_name or str(metadata.get("display_name") or "").strip()

        display_name = _trusted_principal_display_name(display_name, construct_id)
        return {
            "principalId": construct_id,
            "principalType": "construct",
            "displayName": display_name,
            "surface": surface,
            "role": role,
            "avatarUrl": f"/api/ais/{construct_id}/avatar",
            "active": True,
        }

    def _assert_singleton(
        self,
        cur: Any,
        owner_user_id: str,
        target_construct_id: str,
        thread_id: str,
    ) -> None:
        expected_thread_id = f"{target_construct_id}_chat_with_{target_construct_id}"
        if thread_id != expected_thread_id:
            raise ConversationContractError(
                "SINGLETON_THREAD_MISMATCH",
                "The requested thread is not the target construct's canonical singleton",
                409,
            )
        target = chatty_body_service._transcript_target(target_construct_id)
        cur.execute(
            """SELECT id
                 FROM ovvaults.transcripts
                WHERE user_id::text=%s
                  AND lower(title)=lower(%s)
                  AND content IS NOT NULL
                  AND content <> ''
                ORDER BY created_at DESC
                LIMIT 1""",
            (str(owner_user_id), target["storage_path"]),
        )
        if not cur.fetchone():
            raise ConversationContractError(
                "SINGLETON_TRANSCRIPT_NOT_FOUND",
                "The existing canonical singleton transcript was not found",
                404,
            )

    @staticmethod
    def _active_incarnation(cur: Any, owner_user_id: str, construct_id: str) -> str:
        cur.execute(
            """SELECT id::text AS incarnation_id
                 FROM ovvaults.construct_incarnations
                WHERE owner_user_id::text=%s
                  AND lower(construct_id)=lower(%s)
                  AND retired_at IS NULL
                ORDER BY generation DESC
                LIMIT 1""",
            (str(owner_user_id), construct_id),
        )
        row = cur.fetchone()
        incarnation_id = str((dict(row) if row else {}).get("incarnation_id") or "")
        if not incarnation_id:
            raise ConversationContractError(
                "CONSTRUCT_INCARNATION_NOT_FOUND",
                "The active canonical construct incarnation was not found",
                409,
            )
        return incarnation_id

    def create_frame(
        self,
        owner_user_id: str,
        target_construct_id: str,
        payload: dict[str, Any],
        *,
        handler_display_name: str = "Owner",
    ) -> dict[str, Any]:
        target_id = _principal_id(target_construct_id, "targetConstructId")
        speaker_id = _principal_id(payload.get("speakerConstructId"), "speakerConstructId")
        same_principal = speaker_id == target_id
        thread_id = _principal_id(payload.get("threadId"), "threadId")
        surface = str(payload.get("surface") or "").strip().lower()
        if not SURFACE_RE.fullmatch(surface):
            raise ConversationContractError("INVALID_FIELD", "surface is invalid")
        if same_principal and surface != "codex":
            raise ConversationContractError(
                "CROSS_SURFACE_ORIGIN_INVALID",
                "Same-principal operation must originate from the signed Codex role",
                403,
            )
        if "onBehalfOf" not in payload or payload.get("onBehalfOf") is not None:
            raise ConversationContractError(
                "DELEGATION_FORBIDDEN",
                "Construct-authored singleton turns require onBehalfOf: null",
                403,
            )

        with self.connect() as conn:
            with conn.cursor() as cur:
                self._assert_singleton(cur, owner_user_id, target_id, thread_id)
                stage_authorization = None
                if same_principal:
                    stage_authorization = self._stage_profile_authorization(
                        payload.get("stageProfileAuthorization")
                    )
                    self._assert_active_stage_profile(
                        cur, owner_user_id, thread_id, stage_authorization
                    )
                speaker = self._construct_principal(
                    cur,
                    owner_user_id,
                    speaker_id,
                    surface=surface,
                    role=("current_author" if same_principal else "third_party_participant"),
                )
                target = self._construct_principal(
                    cur, owner_user_id, target_id, surface="chatty", role="respondent"
                )
                incarnation_id = (
                    self._active_incarnation(cur, owner_user_id, target_id)
                    if same_principal else None
                )

        cross_surface_binding = None
        if same_principal:
            assert stage_authorization is not None
            role_prefix = f"{stage_authorization['stageProfileId']}:{target_id}"
            origin_role_id = f"{role_prefix}:codex-origin"
            respondent_role_id = f"{role_prefix}:chatty-respondent"
            binding_body = {
                "contract": CROSS_SURFACE_BINDING_CONTRACT,
                "authority": "ovvaults",
                "stageProfileId": stage_authorization["stageProfileId"],
                "stageProfileHash": stage_authorization["stageProfileHash"],
                "samePrincipal": True,
                "canonicalPrincipalId": target_id,
                "threadId": thread_id,
                "incarnationId": incarnation_id,
                "originRole": {
                    "roleInstanceId": origin_role_id,
                    "principalId": target_id,
                    "principalType": "construct",
                    "surface": surface,
                    "role": "current_author",
                },
                "respondentRole": {
                    "roleInstanceId": respondent_role_id,
                    "principalId": target_id,
                    "principalType": "construct",
                    "surface": "chatty",
                    "role": "respondent",
                },
                "currentMessagePronouns": {
                    "firstPersonRoleInstanceId": origin_role_id,
                    "secondPersonRoleInstanceId": respondent_role_id,
                },
                "responsePronouns": {
                    "firstPersonRoleInstanceId": respondent_role_id,
                    "secondPersonRoleInstanceId": origin_role_id,
                },
                "handler": {
                    "principalId": str(owner_user_id),
                    "isSpeaker": False,
                    "isProxy": False,
                },
                "onBehalfOf": None,
                "requestMention": f"@{_address(target_id)}",
                "responseMention": f"@{_address(target_id)}",
            }
            cross_surface_binding = {
                **binding_body,
                "bindingHash": hashlib.sha256(
                    json.dumps(
                        binding_body,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
            speaker = {
                **speaker,
                "roleInstanceId": origin_role_id,
            }
            target = {
                **target,
                "roleInstanceId": respondent_role_id,
            }

        addressing = {
            "contract": ADDRESSING_CONTRACT,
            "mode": "explicit_mention",
            "mentionToken": f"@{_address(target_id)}",
            "addressedName": _address(target_id),
            "residentConstructId": target_id,
            "speakerPrincipalId": speaker_id,
            "targetPrincipalId": target_id,
            "targetPrincipalType": "construct",
            "responseMode": "construct_reply",
            "responseAddresseePrincipalId": speaker_id,
            "responseMention": f"@{_address(speaker_id)}",
            **({
                "speakerRoleInstanceId": cross_surface_binding["originRole"]["roleInstanceId"],
                "targetRoleInstanceId": cross_surface_binding["respondentRole"]["roleInstanceId"],
                "responseAddresseeRoleInstanceId": cross_surface_binding["originRole"]["roleInstanceId"],
            } if cross_surface_binding else {}),
        }
        frame = {
            "contract": PARTICIPANT_FRAME_CONTRACT,
            "threadId": thread_id,
            "handler": {
                "principalId": str(owner_user_id),
                "principalType": "human",
                "displayName": str(handler_display_name or "Owner").strip()[:120] or "Owner",
                "authorized": True,
                **({"isSpeaker": False, "isProxy": False} if same_principal else {}),
            },
            "speaker": speaker,
            "addressees": [target],
            "subjects": [],
            "participants": [speaker] if same_principal else [speaker, target],
            "role": SAME_PRINCIPAL_PARTICIPANT_MODE if same_principal else "third_party_participant",
            "onBehalfOf": None,
            "membershipRevision": 1,
            "authority": "ovvaults",
            "addressing": addressing,
            "singletonContract": SINGLETON_FRAME_CONTRACT,
            **({
                "crossSurfaceRoleBinding": cross_surface_binding,
                "stageProfileAuthorization": stage_authorization,
            } if same_principal else {}),
        }
        return {**frame, "signature": sign_payload(frame, self.signing_secret)}

    def verify_frame(
        self,
        owner_user_id: str,
        target_construct_id: str,
        frame_value: Any,
        execution_authorization_value: Any | None = None,
    ) -> dict[str, Any]:
        authorization = (
            _verified_graduation_execution_authorization(
                execution_authorization_value
            )
            if execution_authorization_value is not None
            else None
        )
        if not isinstance(frame_value, dict):
            raise ConversationContractError("PARTICIPANT_FRAME_REQUIRED", "A signed participant frame is required")
        frame = dict(frame_value)
        signature = str(frame.pop("signature", ""))
        if not verify_payload(frame, signature, self.signing_secret):
            raise ConversationContractError("PARTICIPANT_FRAME_SIGNATURE_INVALID", "Participant frame signature is invalid", 403)

        target_id = _principal_id(target_construct_id, "targetConstructId")
        if frame.get("role") == SAME_PRINCIPAL_PARTICIPANT_MODE:
            return self._verify_same_principal_frame(
                owner_user_id,
                target_id,
                frame,
                signature,
                authorization,
            )
        speaker = frame.get("speaker") if isinstance(frame.get("speaker"), dict) else {}
        speaker_id = _principal_id(speaker.get("principalId"), "speaker.principalId")
        thread_id = _principal_id(frame.get("threadId"), "threadId")
        addressees = frame.get("addressees") if isinstance(frame.get("addressees"), list) else []
        addressee_ids = [
            _principal_id(item.get("principalId"), "addressee.principalId")
            for item in addressees
            if isinstance(item, dict)
        ]
        handler = frame.get("handler") if isinstance(frame.get("handler"), dict) else {}
        participants = frame.get("participants") if isinstance(frame.get("participants"), list) else []
        subjects = frame.get("subjects") if isinstance(frame.get("subjects"), list) else []
        expected_thread_id = f"{target_id}_chat_with_{target_id}"
        if (
            frame.get("contract") != PARTICIPANT_FRAME_CONTRACT
            or frame.get("singletonContract") != SINGLETON_FRAME_CONTRACT
            or frame.get("authority") != "ovvaults"
            or frame.get("role") != "third_party_participant"
            or thread_id != expected_thread_id
            or frame.get("onBehalfOf") is not None
            or speaker.get("principalType") != "construct"
            or speaker_id == target_id
            or len(addressee_ids) != 1
            or addressee_ids[0] != target_id
            or subjects
            or len(participants) != 2
            or {str(item.get("principalId") or "") for item in participants if isinstance(item, dict)}
            != {speaker_id, target_id}
            or str(handler.get("principalId") or "") != str(owner_user_id)
            or handler.get("principalType") != "human"
            or handler.get("authorized") is not True
            or str(handler.get("principalId") or "") in {speaker_id, target_id}
            or (
                authorization is not None
                and (
                    authorization["threadId"] != expected_thread_id
                    or authorization["constructId"] != target_id
                    or authorization.get("evaluatorConstructPrincipalId") != speaker_id
                    or authorization.get("respondentConstructPrincipalId") != target_id
                    or authorization.get("expectedResponseAddresseePrincipalId") != speaker_id
                    or str(authorization["taskIdentity"].get("ownerPrincipalId") or "")
                    != str(owner_user_id)
                )
            )
        ):
            raise ConversationContractError("PARTICIPANT_FRAME_SCOPE_INVALID", "Participant frame scope is invalid", 403)

        addressing = frame.get("addressing") if isinstance(frame.get("addressing"), dict) else {}
        if (
            addressing.get("contract") != ADDRESSING_CONTRACT
            or addressing.get("mode") != "explicit_mention"
            or addressing.get("mentionToken") != f"@{_address(target_id)}"
            or addressing.get("addressedName") != _address(target_id)
            or addressing.get("residentConstructId") != target_id
            or addressing.get("speakerPrincipalId") != speaker_id
            or addressing.get("targetPrincipalId") != target_id
            or addressing.get("targetPrincipalType") != "construct"
            or addressing.get("responseMode") != "construct_reply"
            or addressing.get("responseAddresseePrincipalId") != speaker_id
            or addressing.get("responseMention") != f"@{_address(speaker_id)}"
            or (
                authorization is not None
                and authorization.get("expectedResponseMentionSha256")
                != hashlib.sha256(
                    str(addressing.get("responseMention") or "").encode("utf-8")
                ).hexdigest()
            )
        ):
            raise ConversationContractError("ADDRESSING_SCOPE_MISMATCH", "Signed addressing does not match the singleton turn", 403)

        with self.connect() as conn:
            with conn.cursor() as cur:
                self._assert_singleton(cur, owner_user_id, target_id, thread_id)
                resolved_speaker = self._construct_principal(
                    cur,
                    owner_user_id,
                    speaker_id,
                    surface=str(speaker.get("surface") or "codex"),
                    role="third_party_participant",
                )
                resolved_target = self._construct_principal(
                    cur, owner_user_id, target_id, surface="chatty", role="respondent"
                )

        target = next(
            (item for item in addressees if isinstance(item, dict) and item.get("principalId") == target_id),
            None,
        )
        if (
            str(speaker.get("displayName") or "") != resolved_speaker["displayName"]
            or not isinstance(target, dict)
            or str(target.get("displayName") or "") != resolved_target["displayName"]
        ):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_SCOPE_INVALID",
                "Participant frame identity labels no longer match canonical principals",
                403,
            )
        return {
            "contract": "chatty-canonical-authorship/v1",
            "handler": handler,
            "speaker": speaker,
            "target": target,
            "threadId": thread_id,
            "surface": str(speaker.get("surface") or "codex"),
            "onBehalfOf": None,
            "participantFrame": {**frame, "signature": signature},
            "participantFrameSignature": signature,
            "authority": "ovvaults",
            **({
                "authorizedTurnId": authorization["turnId"],
                "graduationExecutionAuthorizationHash": authorization["executionAuthorizationHash"],
                "evaluatorConstructPrincipalId": authorization["evaluatorConstructPrincipalId"],
                "respondentConstructPrincipalId": authorization["respondentConstructPrincipalId"],
                "responseAddresseePrincipalId": authorization["expectedResponseAddresseePrincipalId"],
                "responseMentionSha256": authorization["expectedResponseMentionSha256"],
            } if authorization is not None else {}),
        }

    def _verify_same_principal_frame(
        self,
        owner_user_id: str,
        target_id: str,
        frame: dict[str, Any],
        signature: str,
        authorization: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Verify one canonical construct occupying two signed surface roles."""
        thread_id = _principal_id(frame.get("threadId"), "threadId")
        expected_thread_id = f"{target_id}_chat_with_{target_id}"
        speaker = frame.get("speaker") if isinstance(frame.get("speaker"), dict) else {}
        handler = frame.get("handler") if isinstance(frame.get("handler"), dict) else {}
        addressees = frame.get("addressees") if isinstance(frame.get("addressees"), list) else []
        participants = frame.get("participants") if isinstance(frame.get("participants"), list) else []
        subjects = frame.get("subjects") if isinstance(frame.get("subjects"), list) else []
        target = addressees[0] if len(addressees) == 1 and isinstance(addressees[0], dict) else {}
        binding = (
            frame.get("crossSurfaceRoleBinding")
            if isinstance(frame.get("crossSurfaceRoleBinding"), dict)
            else {}
        )
        stage_authorization = self._stage_profile_authorization(
            frame.get("stageProfileAuthorization")
        )
        origin = binding.get("originRole") if isinstance(binding.get("originRole"), dict) else {}
        respondent = (
            binding.get("respondentRole")
            if isinstance(binding.get("respondentRole"), dict)
            else {}
        )
        current_pronouns = (
            binding.get("currentMessagePronouns")
            if isinstance(binding.get("currentMessagePronouns"), dict)
            else {}
        )
        response_pronouns = (
            binding.get("responsePronouns")
            if isinstance(binding.get("responsePronouns"), dict)
            else {}
        )
        binding_hash = str(binding.get("bindingHash") or "")
        binding_body = {key: value for key, value in binding.items() if key != "bindingHash"}
        expected_binding_hash = hashlib.sha256(
            json.dumps(
                binding_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        origin_role_id = str(origin.get("roleInstanceId") or "")
        respondent_role_id = str(respondent.get("roleInstanceId") or "")
        expected_binding_keys = {
            "contract", "authority", "stageProfileId", "stageProfileHash",
            "samePrincipal", "canonicalPrincipalId", "threadId", "incarnationId",
            "originRole", "respondentRole", "currentMessagePronouns",
            "responsePronouns", "handler", "onBehalfOf", "requestMention",
            "responseMention", "bindingHash",
        }
        binding_handler = (
            binding.get("handler") if isinstance(binding.get("handler"), dict) else {}
        )
        if (
            frame.get("contract") != PARTICIPANT_FRAME_CONTRACT
            or frame.get("singletonContract") != SINGLETON_FRAME_CONTRACT
            or frame.get("authority") != "ovvaults"
            or frame.get("onBehalfOf") is not None
            or thread_id != expected_thread_id
            or speaker.get("principalId") != target_id
            or speaker.get("principalType") != "construct"
            or speaker.get("surface") != "codex"
            or speaker.get("role") != "current_author"
            or speaker.get("roleInstanceId") != origin_role_id
            or target.get("principalId") != target_id
            or target.get("principalType") != "construct"
            or target.get("surface") != "chatty"
            or target.get("role") != "respondent"
            or target.get("roleInstanceId") != respondent_role_id
            or subjects
            or len(participants) != 1
            or not isinstance(participants[0], dict)
            or participants[0].get("principalId") != target_id
            or str(handler.get("principalId") or "") != str(owner_user_id)
            or handler.get("principalType") != "human"
            or handler.get("authorized") is not True
            or handler.get("isSpeaker") is not False
            or handler.get("isProxy") is not False
            or str(handler.get("principalId") or "") == target_id
            or binding.get("contract") != CROSS_SURFACE_BINDING_CONTRACT
            or set(binding) != expected_binding_keys
            or binding.get("authority") != "ovvaults"
            or binding.get("stageProfileId") != stage_authorization["stageProfileId"]
            or binding.get("stageProfileHash") != stage_authorization["stageProfileHash"]
            or binding.get("samePrincipal") is not True
            or binding.get("canonicalPrincipalId") != target_id
            or binding.get("threadId") != thread_id
            or binding.get("onBehalfOf") is not None
            or binding.get("requestMention") != f"@{_address(target_id)}"
            or binding.get("responseMention") != f"@{_address(target_id)}"
            or binding_handler != {
                "principalId": str(owner_user_id),
                "isSpeaker": False,
                "isProxy": False,
            }
            or binding_hash != expected_binding_hash
            or not origin_role_id
            or not respondent_role_id
            or origin_role_id == respondent_role_id
            or origin.get("principalId") != target_id
            or origin.get("principalType") != "construct"
            or origin.get("surface") != "codex"
            or origin.get("role") != "current_author"
            or respondent.get("principalId") != target_id
            or respondent.get("principalType") != "construct"
            or respondent.get("surface") != "chatty"
            or respondent.get("role") != "respondent"
            or current_pronouns != {
                "firstPersonRoleInstanceId": origin_role_id,
                "secondPersonRoleInstanceId": respondent_role_id,
            }
            or response_pronouns != {
                "firstPersonRoleInstanceId": respondent_role_id,
                "secondPersonRoleInstanceId": origin_role_id,
            }
        ):
            raise ConversationContractError(
                "CROSS_SURFACE_PARTICIPANT_FRAME_SCOPE_INVALID",
                "Same-principal cross-surface participant frame scope is invalid",
                403,
            )
        addressing = frame.get("addressing") if isinstance(frame.get("addressing"), dict) else {}
        expected_mention = f"@{_address(target_id)}"
        if (
            addressing.get("contract") != ADDRESSING_CONTRACT
            or addressing.get("mode") != "explicit_mention"
            or addressing.get("mentionToken") != expected_mention
            or addressing.get("responseMention") != expected_mention
            or addressing.get("residentConstructId") != target_id
            or addressing.get("speakerPrincipalId") != target_id
            or addressing.get("targetPrincipalId") != target_id
            or addressing.get("responseAddresseePrincipalId") != target_id
            or addressing.get("speakerRoleInstanceId") != origin_role_id
            or addressing.get("targetRoleInstanceId") != respondent_role_id
            or addressing.get("responseAddresseeRoleInstanceId") != origin_role_id
        ):
            raise ConversationContractError(
                "ADDRESSING_SCOPE_MISMATCH",
                "Signed cross-surface addressing does not match the singleton turn",
                403,
            )
        if authorization is not None and (
            # stageProfileStateReceiptHash is intentionally not compared here.
            # It is a signed append-only lifecycle head that may rotate between
            # exact-turn preflight and persistence. Stable authority remains
            # bound by program/profile/stage/case plus the signed role binding;
            # the active owner-qualified profile is resolved independently below.
            authorization.get("participantMode") != SAME_PRINCIPAL_PARTICIPANT_MODE
            or authorization.get("programId") != stage_authorization["programId"]
            or authorization.get("stageProfileId") != stage_authorization["stageProfileId"]
            or authorization.get("stageProfileHash") != stage_authorization["stageProfileHash"]
            or authorization.get("stageId") != stage_authorization["stageId"]
            or authorization.get("stageCaseId") != stage_authorization["stageCaseId"]
            or authorization.get("crossSurfaceRoleBindingHash") != binding_hash
            or authorization.get("threadId") != thread_id
            or authorization.get("constructId") != target_id
            or authorization.get("evaluatorConstructPrincipalId") != target_id
            or authorization.get("respondentConstructPrincipalId") != target_id
            or authorization.get("expectedResponseAddresseePrincipalId") != target_id
            or authorization.get("expectedResponseMentionSha256")
            != hashlib.sha256(expected_mention.encode("utf-8")).hexdigest()
            or str(authorization.get("taskIdentity", {}).get("ownerPrincipalId") or "")
            != str(owner_user_id)
        ):
            raise ConversationContractError(
                "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
                "Graduation execution authorization does not match the signed cross-surface frame",
                403,
            )

        with self.connect() as conn:
            with conn.cursor() as cur:
                self._assert_singleton(cur, owner_user_id, target_id, thread_id)
                self._assert_active_stage_profile(
                    cur, owner_user_id, thread_id, stage_authorization
                )
                active_incarnation_id = self._active_incarnation(
                    cur, owner_user_id, target_id
                )
                resolved_origin = self._construct_principal(
                    cur, owner_user_id, target_id, surface="codex", role="current_author"
                )
                resolved_respondent = self._construct_principal(
                    cur, owner_user_id, target_id, surface="chatty", role="respondent"
                )
        if (
            speaker.get("displayName") != resolved_origin["displayName"]
            or target.get("displayName") != resolved_respondent["displayName"]
            or binding.get("incarnationId") != active_incarnation_id
        ):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_SCOPE_INVALID",
                "Participant frame identity labels no longer match the canonical principal",
                403,
            )
        return {
            "contract": "chatty-canonical-authorship/v1",
            "handler": handler,
            "speaker": speaker,
            "target": target,
            "threadId": thread_id,
            "surface": "codex",
            "responseSurface": "chatty",
            "onBehalfOf": None,
            "participantMode": SAME_PRINCIPAL_PARTICIPANT_MODE,
            "crossSurfaceRoleBinding": binding,
            "crossSurfaceRoleBindingHash": binding_hash,
            "stageProfileAuthorization": stage_authorization,
            "participantFrame": {**frame, "signature": signature},
            "participantFrameSignature": signature,
            "authority": "ovvaults",
            **({
                "authorizedTurnId": authorization["turnId"],
                "graduationExecutionAuthorizationHash": authorization["executionAuthorizationHash"],
                "evaluatorConstructPrincipalId": target_id,
                "respondentConstructPrincipalId": target_id,
                "responseAddresseePrincipalId": target_id,
                "responseMentionSha256": authorization["expectedResponseMentionSha256"],
            } if authorization is not None else {}),
        }


singleton_construct_authorization_service = SingletonConstructAuthorizationService()
