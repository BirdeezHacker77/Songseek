from infrastructure.msgspec_fastapi import AppStruct


class ImportReviewAcceptResponse(AppStruct):
    """How many files the answer actually rewrote.

    Zero is a normal outcome for an accept: the download may already have
    carried the release's tags, and the review was only ever asking whether it
    was the right release.
    """

    files_written: int = 0
