import os
from pocketflow import Node
from src.utils.crawl_github_files import crawl_github_files
from src.utils.crawl_local_files import crawl_local_files
from src.utils.git_clone import clone_repository, cleanup_temp_repo


class FetchRepo(Node):
    def prep(self, shared):
        # Store shared context for SSE callbacks
        self._shared = shared
        
        repo_url = shared.get("repo_url")
        local_dir = shared.get("local_dir")
        project_name = shared.get("project_name")

        if not project_name:
            # Basic name derivation from URL or directory
            if repo_url:
                project_name = repo_url.split("/")[-1].replace(".git", "")
            else:
                project_name = os.path.basename(os.path.abspath(local_dir))
            shared["project_name"] = project_name

        # Get file patterns directly from shared
        include_patterns = shared["include_patterns"]
        exclude_patterns = shared["exclude_patterns"]
        max_file_size = shared["max_file_size"]

        return {
            "repo_url": repo_url,
            "local_dir": local_dir,
            "token": shared.get("github_token"),
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "max_file_size": max_file_size,
            "use_relative_paths": True,
        }

    def exec(self, prep_res):
        # Send SSE update if callback available
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "FetchRepo",
                "status": "starting",
                "message": "Fetching repository files..."
            })

        temp_repo_path = None
        
        if prep_res["repo_url"]:
            # Try git clone first (avoids API rate limits)
            try:
                # Send SSE update
                if hasattr(self, '_shared') and 'sse_callback' in self._shared:
                    callback = self._shared['sse_callback']
                    callback("node_progress", {
                        "node": "FetchRepo",
                        "status": "cloning",
                        "message": f"Cloning repository with git..."
                    })
                
                # Clone repository to temp directory
                temp_repo_path = clone_repository(prep_res["repo_url"])
                
                # Store temp path for cleanup
                if hasattr(self, '_shared'):
                    self._shared['_temp_repo_path'] = temp_repo_path
                
                # Crawl the cloned local repository
                print(f"Crawling cloned repository: {temp_repo_path}...")
                result = crawl_local_files(
                    directory=temp_repo_path,
                    include_patterns=prep_res["include_patterns"],
                    exclude_patterns=prep_res["exclude_patterns"],
                    max_file_size=prep_res["max_file_size"],
                    use_relative_paths=prep_res["use_relative_paths"]
                )
                
            except Exception as e:
                # Fallback to GitHub API if git clone fails
                print(f"Git clone failed: {e}. Falling back to GitHub API...")
                
                # Clean up temp directory if it exists
                if temp_repo_path and os.path.exists(temp_repo_path):
                    cleanup_temp_repo(temp_repo_path)
                
                # Send SSE update
                if hasattr(self, '_shared') and 'sse_callback' in self._shared:
                    callback = self._shared['sse_callback']
                    callback("node_progress", {
                        "node": "FetchRepo",
                        "status": "fallback",
                        "message": "Using GitHub API (git clone failed)..."
                    })
                
                result = crawl_github_files(
                    repo_url=prep_res["repo_url"],
                    token=prep_res["token"],
                    include_patterns=prep_res["include_patterns"],
                    exclude_patterns=prep_res["exclude_patterns"],
                    max_file_size=prep_res["max_file_size"],
                    use_relative_paths=prep_res["use_relative_paths"],
                )
        else:
            print(f"Crawling directory: {prep_res['local_dir']}...")

            result = crawl_local_files(
                directory=prep_res["local_dir"],
                include_patterns=prep_res["include_patterns"],
                exclude_patterns=prep_res["exclude_patterns"],
                max_file_size=prep_res["max_file_size"],
                use_relative_paths=prep_res["use_relative_paths"]
            )

        # Convert dict to list of tuples: [(path, content), ...]
        files_list = list(result.get("files", {}).items())
        if len(files_list) == 0:
            raise (ValueError("Failed to fetch files"))
        
        print(f"Fetched {len(files_list)} files.")
        
        # Send SSE update
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "FetchRepo",
                "status": "completed",
                "message": f"Successfully fetched {len(files_list)} files"
            })
        
        return files_list

    def post(self, shared, prep_res, exec_res):
        shared["files"] = exec_res  # List of (path, content) tuples