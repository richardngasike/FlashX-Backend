from django.contrib import admin

from .models import Conversation, ConversationParticipant, Message


class ParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 0
    raw_id_fields = ("user",)
    readonly_fields = ("last_read_at", "joined_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Metadata only: message bodies are deliberately not listed here."""

    list_display = ("id", "is_group", "title", "member_count", "last_message_at", "created_at")
    list_filter = ("is_group", "created_at")
    search_fields = ("title", "memberships__user__username")
    inlines = (ParticipantInline,)
    readonly_fields = ("direct_key", "last_message_at", "created_at", "updated_at")

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.memberships.count()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "kind", "is_read", "is_deleted", "created_at")
    list_filter = ("media_type", "is_read", "is_deleted", "created_at")
    search_fields = ("sender__username", "conversation__id")
    raw_id_fields = ("conversation", "sender", "reply_to", "shared_post", "shared_reel", "story", "asset")
    exclude = ("content",)
    readonly_fields = ("media_type", "media_public_id", "is_read", "created_at", "deleted_at")
    date_hierarchy = "created_at"
    list_select_related = ("sender",)

    @admin.display(description="Kind")
    def kind(self, obj):
        if obj.media_type:
            return obj.media_type
        if obj.shared_post_id:
            return "shared post"
        if obj.shared_reel_id:
            return "shared reel"
        if obj.story_id:
            return "story reply"
        return "text"
