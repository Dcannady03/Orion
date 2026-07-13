"""Build concise AI context from Orion services."""
from __future__ import annotations


class ContextBuilder:
    def __init__(self, conversation, memory=None, project_context=None, knowledge_index=None, max_messages: int = 8, max_chars: int = 6000):
        self.conversation = conversation
        self.memory = memory
        self.project_context = project_context
        self.knowledge_index = knowledge_index
        self.max_messages = max_messages
        self.max_chars = max_chars

    def build(self) -> str:
        sections: list[str] = []
        messages = self.conversation.recent(self.max_messages)
        if messages:
            lines = [f"{message.role.title()}: {message.content}" for message in messages]
            sections.append("Recent conversation:\n" + "\n".join(lines))
        if self.memory is not None:
            items = self.memory.all()
            if items:
                lines = [f"- {key}: {value}" for key, value in items.items()]
                sections.append("Session memory:\n" + "\n".join(lines))
        if self.project_context is not None and self.project_context.initialized:
            project = self.project_context.project()
            project_lines = [
                f"- Name: {project.get('name', '')}",
                f"- Phase: {project.get('phase', '')}",
                f"- Goal: {project.get('current_goal', '')}",
            ]
            checkpoint = self.project_context.latest_checkpoint()
            if checkpoint:
                project_lines.extend([
                    f"- Last checkpoint: {checkpoint.get('summary', '')}",
                    f"- Current task: {checkpoint.get('current_task', '')}",
                    f"- Next step: {checkpoint.get('next_step', '')}",
                ])
            sections.append("Active project:\n" + "\n".join(project_lines))
            rules = self.project_context.rules()
            if rules:
                sections.append("Mandatory project rules (must be followed):\n" + "\n".join(f"- {item['rule']}" for item in rules))
        if self.knowledge_index is not None:
            summary = self.knowledge_index.summary()
            if summary:
                sections.append(summary)
        result = "\n\n".join(sections)
        return result[-self.max_chars:]
