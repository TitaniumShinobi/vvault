"""
Affect API Routes

Flask routes for affect operations.
"""

from flask import Blueprint, request, jsonify
from typing import Dict, Any, Optional
import os

from .affect_service import AffectService
from ..models.affective_state import UserSignal


def create_affect_blueprint(vvault_root: Optional[str] = None) -> Blueprint:
    """
    Create Flask blueprint for affect API routes
    
    Args:
        vvault_root: Root path to VVAULT filesystem (defaults to env var or relative path)
    
    Returns:
        Flask Blueprint
    """
    # Determine VVAULT root
    if vvault_root is None:
        vvault_root = os.environ.get('VVAULT_ROOT', os.path.join(os.path.dirname(__file__), '../../..'))
    
    # Initialize service
    affect_service = AffectService(vvault_root)
    
    # Create blueprint
    affect_bp = Blueprint('affect', __name__, url_prefix='/api/affect')
    
    @affect_bp.route('/state/<user_id>/<construct_callsign>', methods=['GET'])
    def get_state(user_id: str, construct_callsign: str):
        """Get current affective state"""
        try:
            state = affect_service.get_state(user_id, construct_callsign)
            return jsonify({
                "ok": True,
                "state": state.to_dict()
            }), 200
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500
    
    @affect_bp.route('/state/<user_id>/<construct_callsign>', methods=['POST'])
    def update_state(user_id: str, construct_callsign: str):
        """Update affective state"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    "ok": False,
                    "error": "Missing request body"
                }), 400
            
            # Extract user signal
            user_signal = data.get("userSignal", {})
            if not user_signal:
                return jsonify({
                    "ok": False,
                    "error": "Missing userSignal"
                }), 400
            
            # Extract optional interaction history
            interaction_history = data.get("interactionHistory", None)
            
            # Update state
            new_state, update_result = affect_service.update_state(
                user_id,
                construct_callsign,
                user_signal,
                interaction_history
            )
            
            return jsonify({
                "ok": True,
                "state": new_state.to_dict(),
                "updateResult": update_result
            }), 200
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500
    
    @affect_bp.route('/history/<user_id>/<construct_callsign>', methods=['GET'])
    def get_history(user_id: str, construct_callsign: str):
        """Get state history"""
        try:
            limit = request.args.get('limit', type=int)
            history = affect_service.get_history(user_id, construct_callsign, limit)
            return jsonify({
                "ok": True,
                "history": history
            }), 200
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500
    
    @affect_bp.route('/inspect/<user_id>/<construct_callsign>', methods=['GET'])
    def inspect_state(user_id: str, construct_callsign: str):
        """Inspect state (read-only, includes governance status)"""
        try:
            state = affect_service.get_state(user_id, construct_callsign)
            history = affect_service.get_history(user_id, construct_callsign, limit=10)
            
            return jsonify({
                "ok": True,
                "state": state.to_dict(),
                "recentHistory": history,
                "governanceStatus": state.governance_status
            }), 200
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500
    
    @affect_bp.route('/reset/<user_id>/<construct_callsign>', methods=['POST'])
    def reset_state(user_id: str, construct_callsign: str):
        """Reset state to default"""
        try:
            success = affect_service.reset_state(user_id, construct_callsign)
            if success:
                state = affect_service.get_state(user_id, construct_callsign)
                return jsonify({
                    "ok": True,
                    "state": state.to_dict(),
                    "message": "State reset to default"
                }), 200
            else:
                return jsonify({
                    "ok": False,
                    "error": "Failed to reset state"
                }), 500
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500
    
    return affect_bp

