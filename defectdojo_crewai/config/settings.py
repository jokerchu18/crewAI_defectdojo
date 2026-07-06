class Settings:
    def __init__(self):
        self.defectdojo_base_url = "http://localhost:8080"
        self.defectdojo_api_key = "2e42678cc470e6cd3b0355306e7ee8e9daf0d492"
        self.defectdojo_engagement_id = 1

        self.default_scan_type = "SARIF"
        self.default_scan_file_path = r"D:\github\crewAI_defectdojo\sample_reports\sample.sarif"

        self.crew_verbose = True


settings = Settings()