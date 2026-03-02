export class AppError extends Error {
  constructor(
    public statusCode: number,
    public message: string,
    public code?: string,
    public details?: any[]
  ) {
    super(message);
    Error.captureStackTrace(this, this.constructor);
  }
}
