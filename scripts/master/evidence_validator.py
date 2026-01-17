def validate_evidence(highlights):
    validated_highlights = []
    for highlight in highlights:
        if len(highlight.split()) > 5:
            validated_highlights.append(highlight)
    return validated_highlights
