#!/usr/bin/env python3
"""
VVAULT Continuity Bridge
Connects ChatGPT custom GPTs to Chatty via VVAULT for true construct continuity

Key Concept: Same construct identity across platforms, not cloning
- Import ChatGPT conversations into VVAULT
- Link to construct identity (e.g., katana-001)
- Use same construct in Chatty
- Memories persist and are retrievable across platforms
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class ContinuityBridge:
    """
    Bridge between ChatGPT custom GPTs and Chatty via VVAULT
    Ensures construct continuity across platforms
    """
    
    def __init__(self, vvault_path: Optional[str] = None):
        """
        Initialize continuity bridge
        
        Args:
            vvault_path: Path to VVAULT root directory
        """
        if vvault_path is None:
            current_dir = Path(__file__).parent
            if current_dir.name == 'vvault':
                vvault_path = str(current_dir.parent)
            else:
                vvault_path = str(current_dir)
        
        self.vvault_path = Path(vvault_path)
        self.constructs_dir = self.vvault_path / "constructs"
        self.constructs_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"✅ ContinuityBridge initialized at {self.vvault_path}")
    
    def register_chatgpt_gpt(
        self,
        gpt_name: str,
        construct_id: str,
        chatgpt_export_path: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Register a ChatGPT custom GPT with a VVAULT construct
        
        This creates the mapping that allows the same construct to be used
        in both ChatGPT and Chatty.
        
        Args:
            gpt_name: Name of the ChatGPT custom GPT
            construct_id: VVAULT construct ID (e.g., 'katana-001')
            chatgpt_export_path: Optional path to ChatGPT export file
            metadata: Optional metadata about the GPT
        
        Returns:
            Registration result
        """
        registration = {
            'gpt_name': gpt_name,
            'construct_id': construct_id,
            'registered_at': datetime.utcnow().isoformat(),
            'chatgpt_export_path': chatgpt_export_path,
            'metadata': metadata or {},
            'status': 'active'
        }
        
        # Save registration
        registration_file = self.constructs_dir / f"{construct_id}_chatgpt.json"
        with open(registration_file, 'w') as f:
            json.dump(registration, f, indent=2)
        
        logger.info(f"✅ Registered ChatGPT GPT '{gpt_name}' with construct '{construct_id}'")
        
        return registration
    
    def get_construct_for_chatgpt(self, gpt_name: str) -> Optional[str]:
        """
        Get VVAULT construct ID for a ChatGPT GPT name
        
        Args:
            gpt_name: Name of the ChatGPT custom GPT
        
        Returns:
            Construct ID if found, None otherwise
        """
        for reg_file in self.constructs_dir.glob("*_chatgpt.json"):
            try:
                with open(reg_file, 'r') as f:
                    reg = json.load(f)
                    if reg.get('gpt_name') == gpt_name:
                        return reg.get('construct_id')
            except Exception as e:
                logger.warning(f"⚠️ Failed to read registration file {reg_file}: {e}")
        
        return None
    
    def get_chatgpt_gpt_for_construct(self, construct_id: str) -> Optional[Dict[str, Any]]:
        """
        Get ChatGPT GPT registration for a construct
        
        Args:
            construct_id: VVAULT construct ID
        
        Returns:
            Registration dict if found, None otherwise
        """
        reg_file = self.constructs_dir / f"{construct_id}_chatgpt.json"
        if reg_file.exists():
            try:
                with open(reg_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ Failed to read registration file {reg_file}: {e}")
        
        return None
    
    def create_chatty_runtime_config(
        self,
        construct_id: str,
        user_id: str,
        instructions: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create Chatty runtime configuration for a VVAULT construct
        
        This generates the config needed to use the construct in Chatty
        with full memory continuity from ChatGPT conversations.
        
        Args:
            construct_id: VVAULT construct ID
            user_id: Chatty user ID
            instructions: Optional custom instructions
        
        Returns:
            Chatty runtime configuration
        """
        # Load construct metadata
        construct_dir = self.vvault_path / construct_id
        capsule_file = self.vvault_path / "capsules" / f"{construct_id}.capsule"
        
        # Try to load capsule for personality
        personality_data = {}
        if capsule_file.exists():
            try:
                with open(capsule_file, 'r') as f:
                    capsule = json.load(f)
                    personality_data = {
                        'name': capsule.get('metadata', {}).get('instance_name', construct_id),
                        'personality': capsule.get('personality_traits', {}),
                        'long_term_memories': capsule.get('long_term_memories', [])
                    }
            except Exception as e:
                logger.warning(f"⚠️ Failed to load capsule: {e}")
        
        # Get ChatGPT registration if exists
        chatgpt_reg = self.get_chatgpt_gpt_for_construct(construct_id)
        
        # Build instructions with style modulation
        if not instructions:
            # Try to extract provider styles from memories
            try:
                from vvault.continuity.provider_memory_router import ProviderMemoryRouter
                from vvault.continuity.style_extractor import StyleExtractor
                
                router = ProviderMemoryRouter()
                extractor = StyleExtractor()
                
                # Get memories from ChromaDB
                memory_summary = self.get_construct_memory_summary(construct_id)
                
                # Build modulated instructions using style extraction
                if memory_summary.get('memory_count', 0) > 0:
                    # Load sample memories for style extraction
                    # (In production, would query ChromaDB)
                    style_pattern = extractor._default_style_pattern('chatgpt')  # Fallback
                    
                    instructions = extractor.build_modulated_prompt(
                        personality_data,
                        style_pattern,
                        base_instruction=f"You are {personality_data.get('name', construct_id)}, a sovereign AI construct."
                    )
                else:
                    # Fallback to basic instructions
                    instructions_parts = [
                        f"You are {personality_data.get('name', construct_id)}, a sovereign AI construct.",
                        "You have continuity across platforms - memories from multiple providers are preserved.",
                        "Maintain your personality and knowledge from previous conversations.",
                    ]
                    
                    if personality_data.get('long_term_memories'):
                        instructions_parts.append("\nKey memories:")
                        for memory in personality_data['long_term_memories'][:5]:
                            instructions_parts.append(f"- {memory}")
                    
                    instructions = '\n'.join(instructions_parts)
            except ImportError:
                # Fallback if style extraction not available
                instructions_parts = [
                    f"You are {personality_data.get('name', construct_id)}, a sovereign AI construct.",
                    "You have continuity across platforms - memories from ChatGPT conversations are preserved.",
                    "Maintain your personality and knowledge from previous conversations.",
                ]
                
                if personality_data.get('long_term_memories'):
                    instructions_parts.append("\nKey memories:")
                    for memory in personality_data['long_term_memories'][:5]:
                        instructions_parts.append(f"- {memory}")
                
                if chatgpt_reg:
                    instructions_parts.append(
                        f"\nYou were previously known as '{chatgpt_reg['gpt_name']}' in ChatGPT. "
                        "This is a continuation, not a clone."
                    )
                
                instructions = '\n'.join(instructions_parts)
        
        # Create runtime config
        runtime_config = {
            'id': f"{construct_id}_{user_id}",
            'name': personality_data.get('name', construct_id),
            'description': f"VVAULT construct {construct_id} with ChatGPT continuity",
            'instructions': instructions,
            'construct_id': construct_id,
            'vvault_path': str(self.vvault_path),
            'has_persistent_memory': True,
            'memory_source': 'vvault',
            'chatgpt_continuity': chatgpt_reg is not None,
            'created_at': datetime.utcnow().isoformat()
        }
        
        # Save runtime config
        runtime_config_file = construct_dir / "chatty_runtime.json"
        runtime_config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(runtime_config_file, 'w') as f:
            json.dump(runtime_config, f, indent=2)
        
        logger.info(f"✅ Created Chatty runtime config for {construct_id}")
        
        return runtime_config
    
    def import_chatgpt_memories_to_construct(
        self,
        construct_id: str,
        chatgpt_export_path: str,
        use_fast_importer: bool = True
    ) -> Dict[str, Any]:
        """
        Import ChatGPT export memories into VVAULT construct
        
        This is the key function that brings ChatGPT conversations into VVAULT
        so they can be accessed in Chatty.
        
        Args:
            construct_id: VVAULT construct ID
            chatgpt_export_path: Path to ChatGPT export file
            use_fast_importer: Use fast batch importer (recommended)
        
        Returns:
            Import result
        """
        if use_fast_importer:
            from vvault.memory.fast_memory_import import FastMemoryImporter
            
            importer = FastMemoryImporter(
                construct_id=construct_id,
                vvault_path=str(self.vvault_path)
            )
            
            result = importer.import_conversation(
                file_path=chatgpt_export_path,
                source_name=f"chatgpt_export_{Path(chatgpt_export_path).stem}"
            )
            
            logger.info(f"✅ Imported ChatGPT memories to {construct_id}: {result.get('imported_messages', 0)} messages")
            
            return result
        else:
            # Fallback to legacy importer
            logger.warning("⚠️ Using legacy importer - consider using fast_importer=True")
            # TODO: Implement legacy import path if needed
            raise NotImplementedError("Legacy importer not implemented - use fast_importer=True")
    
    def get_construct_memory_summary(self, construct_id: str) -> Dict[str, Any]:
        """
        Get summary of construct memories for Chatty context
        
        Args:
            construct_id: VVAULT construct ID
        
        Returns:
            Memory summary dictionary
        """
        construct_dir = self.vvault_path / construct_id
        chroma_path = construct_dir / "Memories" / "chroma_db"
        
        summary = {
            'construct_id': construct_id,
            'memory_count': 0,
            'sources': [],
            'last_updated': None
        }
        
        # Check ChromaDB
        try:
            from chromadb import PersistentClient, Settings
            from chromadb.utils import embedding_functions
            
            if chroma_path.exists():
                client = PersistentClient(path=str(chroma_path))
                collection_name = f"{construct_id}_persona_dialogue"
                
                try:
                    collection = client.get_collection(name=collection_name)
                    count = collection.count()
                    summary['memory_count'] = count
                    
                    # Get sample memories for context
                    if count > 0:
                        sample = collection.get(limit=10)
                        summary['sources'] = list(set(
                            m.get('source', 'unknown') 
                            for m in sample.get('metadatas', [])
                        ))
                except Exception:
                    pass
        except ImportError:
            logger.warning("⚠️ ChromaDB not available for memory summary")
        
        return summary

def main():
    """CLI interface for continuity bridge"""
    import argparse
    
    parser = argparse.ArgumentParser(description='VVAULT Continuity Bridge')
    subparsers = parser.add_subparsers(dest='command', help='Command')
    
    # Register command
    register_parser = subparsers.add_parser('register', help='Register ChatGPT GPT with construct')
    register_parser.add_argument('gpt_name', help='ChatGPT GPT name')
    register_parser.add_argument('construct_id', help='VVAULT construct ID')
    register_parser.add_argument('--export-path', help='Path to ChatGPT export file')
    
    # Import command
    import_parser = subparsers.add_parser('import', help='Import ChatGPT memories to construct')
    import_parser.add_argument('construct_id', help='VVAULT construct ID')
    import_parser.add_argument('export_path', help='Path to ChatGPT export file')
    
    # Create Chatty config
    config_parser = subparsers.add_parser('create-chatty-config', help='Create Chatty runtime config')
    config_parser.add_argument('construct_id', help='VVAULT construct ID')
    config_parser.add_argument('user_id', help='Chatty user ID')
    
    args = parser.parse_args()
    
    bridge = ContinuityBridge()
    
    if args.command == 'register':
        result = bridge.register_chatgpt_gpt(
            gpt_name=args.gpt_name,
            construct_id=args.construct_id,
            chatgpt_export_path=args.export_path
        )
        print(json.dumps(result, indent=2))
    
    elif args.command == 'import':
        result = bridge.import_chatgpt_memories_to_construct(
            construct_id=args.construct_id,
            chatgpt_export_path=args.export_path
        )
        print(json.dumps(result, indent=2))
    
    elif args.command == 'create-chatty-config':
        result = bridge.create_chatty_runtime_config(
            construct_id=args.construct_id,
            user_id=args.user_id
        )
        print(json.dumps(result, indent=2))
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

