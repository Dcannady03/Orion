"""Build concise AI context from Orion services."""
from __future__ import annotations


class ContextBuilder:
    def __init__(self, conversation, memory=None, project_context=None, max_messages: int = 8, max_chars: int = 6000):
        self.conversation = conversation
        self.memory = memory
        self.project_context = project_context
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
            sections.append(
                "Active project:\n"
                f"- Name: {project.get('name', '')}\n"
                f"- Phase: {project.get('phase', '')}\n"
                f"- Goal: {project.get('current_goal', '')}"
            )
        result = "\n\n".join(sections)
        return result[-self.max_chars:]
