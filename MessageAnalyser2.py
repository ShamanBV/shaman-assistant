"""
Slack Channel Analyzer - Extended Version
==========================================
Analyze questions and patterns in your Slack channels using Claude AI.
Includes: query counts, query types, and resolution rates.

SETUP: See original version for Slack app setup instructions.
"""

import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic

# Load environment variables
load_dotenv()

# Configuration
CHANNEL_NAME = "product-questions"  # Change this to your channel name
DAYS_TO_ANALYZE = 120  # How many days back to look
MAX_MESSAGES = 1000  # Maximum messages to fetch


def get_slack_client():
    """Initialize Slack client."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN not found in environment. Check your .env file.")
    return WebClient(token=token)


def get_anthropic_client():
    """Initialize Anthropic client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in environment. Check your .env file.")
    return anthropic.Anthropic(api_key=api_key)


def find_channel_id(client: WebClient, channel_name: str) -> str:
    """Find channel ID by name."""
    try:
        result = client.conversations_list(types="public_channel,private_channel")
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]

        while result.get("response_metadata", {}).get("next_cursor"):
            result = client.conversations_list(
                types="public_channel,private_channel",
                cursor=result["response_metadata"]["next_cursor"]
            )
            for channel in result["channels"]:
                if channel["name"] == channel_name:
                    return channel["id"]

        raise ValueError(f"Channel '{channel_name}' not found. Make sure the bot is invited to the channel.")

    except SlackApiError as e:
        raise ValueError(f"Error finding channel: {e.response['error']}")


def get_user_names(client: WebClient, user_ids: set) -> dict:
    """Fetch display names for user IDs."""
    user_names = {}
    for user_id in user_ids:
        try:
            result = client.users_info(user=user_id)
            profile = result["user"]["profile"]
            user_names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
        except SlackApiError:
            user_names[user_id] = user_id
    return user_names


def fetch_messages(client: WebClient, channel_id: str, days: int, max_messages: int) -> list:
    """Fetch messages from channel."""
    messages = []
    oldest = datetime.now() - timedelta(days=days)
    oldest_ts = oldest.timestamp()

    try:
        result = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest_ts),
            limit=min(max_messages, 200)
        )
        messages.extend(result["messages"])

        while result.get("has_more") and len(messages) < max_messages:
            result = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest_ts),
                limit=min(max_messages - len(messages), 200),
                cursor=result["response_metadata"]["next_cursor"]
            )
            messages.extend(result["messages"])

        print(f"Fetched {len(messages)} messages from the last {days} days")
        return messages[:max_messages]

    except SlackApiError as e:
        raise ValueError(f"Error fetching messages: {e.response['error']}")


def fetch_thread_replies(client: WebClient, channel_id: str, thread_ts: str) -> list:
    """Fetch replies in a thread."""
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        return result["messages"][1:]  # Exclude parent message
    except SlackApiError:
        return []


def enrich_messages_with_threads(client: WebClient, channel_id: str, messages: list) -> list:
    """Add thread reply data to messages."""
    enriched = []
    thread_count = 0

    for msg in messages:
        if msg.get("subtype"):  # Skip system messages
            continue

        enriched_msg = msg.copy()

        # Fetch thread replies if this message has a thread
        if msg.get("reply_count", 0) > 0:
            thread_count += 1
            replies = fetch_thread_replies(client, channel_id, msg["ts"])
            enriched_msg["thread_replies"] = replies
            enriched_msg["has_replies"] = True
        else:
            enriched_msg["thread_replies"] = []
            enriched_msg["has_replies"] = False

        # Check for reactions (can indicate acknowledgment)
        enriched_msg["has_reactions"] = len(msg.get("reactions", [])) > 0

        enriched.append(enriched_msg)

    print(f"Fetched replies for {thread_count} threads")
    return enriched


def format_messages_for_analysis(messages: list, user_names: dict) -> str:
    """Format messages into readable text for AI analysis."""
    formatted = []

    for i, msg in enumerate(messages, 1):
        user = user_names.get(msg.get("user", ""), "Unknown")
        text = msg.get("text", "")
        ts = datetime.fromtimestamp(float(msg["ts"]))
        date_str = ts.strftime("%Y-%m-%d %H:%M")

        # Replace user mentions with names
        for user_id, name in user_names.items():
            text = text.replace(f"<@{user_id}>", f"@{name}")

        reply_count = len(msg.get("thread_replies", []))
        has_reactions = msg.get("has_reactions", False)

        status_indicators = []
        if reply_count > 0:
            status_indicators.append(f"{reply_count} replies")
        if has_reactions:
            status_indicators.append("has reactions")

        status = f" [{', '.join(status_indicators)}]" if status_indicators else " [no replies]"

        formatted.append(f"[MSG-{i}] [{date_str}] {user}: {text}{status}")

        # Include thread replies
        for reply in msg.get("thread_replies", []):
            reply_user = user_names.get(reply.get("user", ""), "Unknown")
            reply_text = reply.get("text", "")
            for user_id, name in user_names.items():
                reply_text = reply_text.replace(f"<@{user_id}>", f"@{name}")
            formatted.append(f"    └─ {reply_user}: {reply_text}")

    return "\n".join(formatted)


def calculate_basic_stats(messages: list) -> dict:
    """Calculate basic statistics from messages."""
    total = len(messages)
    with_replies = sum(1 for m in messages if m.get("has_replies"))
    with_reactions = sum(1 for m in messages if m.get("has_reactions"))
    with_engagement = sum(1 for m in messages if m.get("has_replies") or m.get("has_reactions"))

    return {
        "total_messages": total,
        "messages_with_replies": with_replies,
        "messages_with_reactions": with_reactions,
        "messages_with_engagement": with_engagement,
        "reply_rate": round(with_replies / total * 100, 1) if total > 0 else 0,
        "engagement_rate": round(with_engagement / total * 100, 1) if total > 0 else 0,
    }


def analyze_with_claude(client: anthropic.Anthropic, messages_text: str, channel_name: str, basic_stats: dict) -> dict:
    """Send messages to Claude for detailed analysis."""

    prompt = f"""Analyze these Slack messages from the #{channel_name} channel.

Return your analysis as JSON with EXACTLY this structure:
{{
    "total_queries": <number of actual questions/requests>,
    "query_types": [
        {{"type": "<category name>", "count": <number>, "percentage": <number>, "examples": ["<brief example>", ...]}},
        ...
    ],
    "resolution_analysis": {{
        "resolved": <number>,
        "unresolved": <number>,
        "unclear": <number>,
        "resolution_rate": <percentage>,
        "resolution_criteria": "<brief explanation of how you determined resolution>"
    }},
    "top_questions": [
        {{"question": "<summarized question>", "frequency": <times asked>, "resolved": <true/false/unclear>}},
        ...
    ],
    "insights": {{
        "common_pain_points": ["<pain point>", ...],
        "feature_requests": ["<request>", ...],
        "knowledge_gaps": ["<topic where users need more documentation>", ...],
        "recommendations": ["<actionable recommendation>", ...]
    }},
    "summary": "<2-3 sentence executive summary>"
}}

Guidelines for analysis:
- A "query" is any question, request for help, or problem report (not casual chat)
- "Resolved" means someone provided a helpful answer OR the asker confirmed resolution
- "Unresolved" means no reply, or replies didn't address the question
- "Unclear" means there's a reply but can't determine if it solved the issue
- Group similar questions together when counting types
- Be specific with examples

MESSAGES:
{messages_text}

Return ONLY valid JSON, no other text.
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse JSON response
    response_text = response.content[0].text

    # Clean up response if needed (remove markdown code blocks)
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    response_text = response_text.strip()

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"Warning: Could not parse JSON response: {e}")
        return {"raw_response": response_text, "parse_error": str(e)}


def print_report(analysis: dict, basic_stats: dict, channel_name: str, days: int):
    """Print formatted analysis report."""

    print("\n" + "=" * 70)
    print(f"SLACK CHANNEL ANALYSIS: #{channel_name}")
    print(f"Period: Last {days} days")
    print("=" * 70)

    # Summary
    if "summary" in analysis:
        print(f"\n📋 SUMMARY")
        print("-" * 70)
        print(analysis["summary"])

    # Key Metrics
    print(f"\n📊 KEY METRICS")
    print("-" * 70)
    print(f"Total messages analyzed:     {basic_stats['total_messages']}")
    print(f"Total queries identified:    {analysis.get('total_queries', 'N/A')}")
    print(f"Messages with replies:       {basic_stats['messages_with_replies']} ({basic_stats['reply_rate']}%)")
    print(f"Messages with engagement:    {basic_stats['messages_with_engagement']} ({basic_stats['engagement_rate']}%)")

    # Resolution Stats
    if "resolution_analysis" in analysis:
        res = analysis["resolution_analysis"]
        print(f"\n✅ RESOLUTION ANALYSIS")
        print("-" * 70)
        print(f"Resolved:                    {res.get('resolved', 'N/A')}")
        print(f"Unresolved:                  {res.get('unresolved', 'N/A')}")
        print(f"Unclear:                     {res.get('unclear', 'N/A')}")
        print(f"Resolution rate:             {res.get('resolution_rate', 'N/A')}%")
        print(f"\nCriteria: {res.get('resolution_criteria', 'N/A')}")

    # Query Types
    if "query_types" in analysis:
        print(f"\n📁 QUERY TYPES")
        print("-" * 70)
        for qt in analysis["query_types"]:
            print(f"\n{qt['type']}: {qt['count']} ({qt['percentage']}%)")
            if qt.get('examples'):
                for ex in qt['examples'][:2]:
                    print(f"   • {ex}")

    # Top Questions
    if "top_questions" in analysis:
        print(f"\n❓ TOP RECURRING QUESTIONS")
        print("-" * 70)
        for q in analysis["top_questions"][:5]:
            status = "✓" if q.get('resolved') == True else ("✗" if q.get('resolved') == False else "?")
            print(f"[{status}] {q['question']} (×{q.get('frequency', 1)})")

    # Insights
    if "insights" in analysis:
        insights = analysis["insights"]

        if insights.get("common_pain_points"):
            print(f"\n🔥 PAIN POINTS")
            print("-" * 70)
            for point in insights["common_pain_points"]:
                print(f"   • {point}")

        if insights.get("feature_requests"):
            print(f"\n💡 FEATURE REQUESTS")
            print("-" * 70)
            for req in insights["feature_requests"]:
                print(f"   • {req}")

        if insights.get("knowledge_gaps"):
            print(f"\n📚 KNOWLEDGE GAPS (need better docs)")
            print("-" * 70)
            for gap in insights["knowledge_gaps"]:
                print(f"   • {gap}")

        if insights.get("recommendations"):
            print(f"\n🎯 RECOMMENDATIONS")
            print("-" * 70)
            for rec in insights["recommendations"]:
                print(f"   • {rec}")

    print("\n" + "=" * 70)


def save_report(analysis: dict, basic_stats: dict, channel_name: str, days: int) -> str:
    """Save analysis to JSON and text files."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Save JSON
    json_file = f"slack_analysis_{channel_name}_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "channel": channel_name,
            "days_analyzed": days,
            "generated_at": datetime.now().isoformat(),
            "basic_stats": basic_stats,
            "analysis": analysis
        }, f, indent=2)

    print(f"✓ JSON saved to: {json_file}")
    return json_file


def main():
    print("=" * 70)
    print("SLACK CHANNEL ANALYZER - Extended Version")
    print("=" * 70)
    print()

    # Initialize clients
    print("Initializing clients...")
    slack_client = get_slack_client()
    anthropic_client = get_anthropic_client()

    # Find channel
    print(f"Looking for channel: #{CHANNEL_NAME}")
    channel_id = find_channel_id(slack_client, CHANNEL_NAME)
    print(f"Found channel ID: {channel_id}")

    # Fetch messages
    print(f"\nFetching messages from the last {DAYS_TO_ANALYZE} days...")
    messages = fetch_messages(slack_client, channel_id, DAYS_TO_ANALYZE, MAX_MESSAGES)

    if not messages:
        print("No messages found in the specified time range.")
        return

    # Get user names
    print("Fetching user information...")
    user_ids = {msg.get("user") for msg in messages if msg.get("user")}
    user_names = get_user_names(slack_client, user_ids)

    # Enrich with thread data
    print("Fetching thread replies...")
    enriched_messages = enrich_messages_with_threads(slack_client, channel_id, messages)

    # Collect user IDs from thread replies too
    for msg in enriched_messages:
        for reply in msg.get("thread_replies", []):
            if reply.get("user"):
                user_ids.add(reply["user"])

    # Fetch any new user names
    for user_id in user_ids:
        if user_id not in user_names:
            try:
                result = slack_client.users_info(user=user_id)
                profile = result["user"]["profile"]
                user_names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
            except SlackApiError:
                user_names[user_id] = user_id

    # Calculate basic stats
    basic_stats = calculate_basic_stats(enriched_messages)

    # Format messages
    print("Formatting messages for analysis...")
    messages_text = format_messages_for_analysis(enriched_messages, user_names)

    # Analyze with Claude
    print("\nAnalyzing with Claude AI (this may take a moment)...")
    analysis = analyze_with_claude(anthropic_client, messages_text, CHANNEL_NAME, basic_stats)

    # Print report
    print_report(analysis, basic_stats, CHANNEL_NAME, DAYS_TO_ANALYZE)

    # Save report
    save_report(analysis, basic_stats, CHANNEL_NAME, DAYS_TO_ANALYZE)


if __name__ == "__main__":
    main()