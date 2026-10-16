export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      app_owner_config: {
        Row: {
          id: boolean
          owner_email: string
        }
        Insert: {
          id?: boolean
          owner_email: string
        }
        Update: {
          id?: boolean
          owner_email?: string
        }
        Relationships: []
      }
      flight_fares: {
        Row: {
          departure: string
          destination: string
          job_id: string
          observed_at: string
          origin: string
          price_eur: number
          provider: string
          return_date: string | null
          search_url: string
        }
        Insert: {
          departure: string
          destination: string
          job_id: string
          observed_at: string
          origin: string
          price_eur: number
          provider: string
          return_date?: string | null
          search_url: string
        }
        Update: {
          departure?: string
          destination?: string
          job_id?: string
          observed_at?: string
          origin?: string
          price_eur?: number
          provider?: string
          return_date?: string | null
          search_url?: string
        }
        Relationships: [
          {
            foreignKeyName: "flight_fares_job_id_fkey"
            columns: ["job_id"]
            isOneToOne: false
            referencedRelation: "flight_jobs"
            referencedColumns: ["id"]
          },
        ]
      }
      flight_jobs: {
        Row: {
          cancel_requested: boolean
          claimed_at: string | null
          created_at: string
          finished_at: string | null
          id: string
          idempotency_key: string
          lease_expires_at: string | null
          lease_token: string | null
          message: string
          owner_id: string
          pairs: Json
          providers: Json
          query: Json
          started_at: string | null
          status: Database["public"]["Enums"]["flight_job_status"]
        }
        Insert: {
          cancel_requested?: boolean
          claimed_at?: string | null
          created_at?: string
          finished_at?: string | null
          id?: string
          idempotency_key: string
          lease_expires_at?: string | null
          lease_token?: string | null
          message?: string
          owner_id: string
          pairs: Json
          providers?: Json
          query: Json
          started_at?: string | null
          status?: Database["public"]["Enums"]["flight_job_status"]
        }
        Update: {
          cancel_requested?: boolean
          claimed_at?: string | null
          created_at?: string
          finished_at?: string | null
          id?: string
          idempotency_key?: string
          lease_expires_at?: string | null
          lease_token?: string | null
          message?: string
          owner_id?: string
          pairs?: Json
          providers?: Json
          query?: Json
          started_at?: string | null
          status?: Database["public"]["Enums"]["flight_job_status"]
        }
        Relationships: []
      }
      flight_worker_status: {
        Row: {
          id: boolean
          last_seen_at: string | null
          state: string | null
          version: string | null
        }
        Insert: {
          id?: boolean
          last_seen_at?: string | null
          state?: string | null
          version?: string | null
        }
        Update: {
          id?: boolean
          last_seen_at?: string | null
          state?: string | null
          version?: string | null
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      flight_cancel: { Args: { _job: string }; Returns: undefined }
      flight_owner_refresh: { Args: never; Returns: undefined }
      flight_parse_date: { Args: { _v: Json }; Returns: string }
      flight_reap: { Args: never; Returns: undefined }
      flight_submit: {
        Args: { _idem: string; _pairs: Json; _query: Json }
        Returns: string
      }
      flight_validate_search: { Args: { _q: Json }; Returns: Json }
      flight_worker_claim: {
        Args: { _lease_seconds: number }
        Returns: {
          cancel_requested: boolean
          claimed_at: string | null
          created_at: string
          finished_at: string | null
          id: string
          idempotency_key: string
          lease_expires_at: string | null
          lease_token: string | null
          message: string
          owner_id: string
          pairs: Json
          providers: Json
          query: Json
          started_at: string | null
          status: Database["public"]["Enums"]["flight_job_status"]
        }[]
        SetofOptions: {
          from: "*"
          to: "flight_jobs"
          isOneToOne: false
          isSetofReturn: true
        }
      }
      flight_worker_finish: {
        Args: {
          _job: string
          _lease: string
          _message: string
          _outcome: string
        }
        Returns: undefined
      }
      flight_worker_heartbeat: {
        Args: { _state: string; _version: string }
        Returns: undefined
      }
      flight_worker_progress: {
        Args: {
          _fares: Json
          _job: string
          _lease: string
          _lease_seconds: number
          _providers: Json
        }
        Returns: boolean
      }
      is_flight_owner: { Args: { _uid?: string }; Returns: boolean }
    }
    Enums: {
      flight_job_status:
        | "queued"
        | "running"
        | "complete"
        | "partial"
        | "failed"
        | "cancelled"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      flight_job_status: [
        "queued",
        "running",
        "complete",
        "partial",
        "failed",
        "cancelled",
      ],
    },
  },
} as const

