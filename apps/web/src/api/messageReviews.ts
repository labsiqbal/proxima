import { api } from "./client";
import type { MessageReview } from "../types";

export const listMessageReviews = (token: string, messageId: number) =>
	api<{ reviews: MessageReview[] }>(
		`/api/messages/${messageId}/reviews`,
		token,
	);

export const createMessageReview = (
	token: string,
	messageId: number,
	body: {
		mode?: "validate" | "brainstorm" | "debate" | "compare";
		reviewer_profile_id?: number | null;
	} = {},
) =>
	api<{ review: MessageReview }>(`/api/messages/${messageId}/reviews`, token, {
		method: "POST",
		body: JSON.stringify(body),
	});

export const replaceAnswerWithReview = (token: string, reviewId: number) =>
	api<{ review: MessageReview; message: { id: number; content: string } }>(
		`/api/message-reviews/${reviewId}/replace-answer`,
		token,
		{ method: "POST" },
	);

export const restoreOriginalAnswer = (token: string, reviewId: number) =>
	api<{ review: MessageReview; message: { id: number; content: string } }>(
		`/api/message-reviews/${reviewId}/restore-original`,
		token,
		{ method: "POST" },
	);

export const askOriginalToRevise = (
	token: string,
	reviewId: number,
	body: { note?: string } = {},
) =>
	api<{
		run_id: number;
		session_id: number;
		status: string;
		review: MessageReview;
	}>(`/api/message-reviews/${reviewId}/ask-original`, token, {
		method: "POST",
		body: JSON.stringify(body),
	});
