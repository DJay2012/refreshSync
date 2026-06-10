@echo off

echo ========================================
echo RefreshES API - Build and Push Docker
echo ========================================
echo.

REM Ask for port
set /p INPUT_PORT="Enter port to expose (default 8000): "
if "%INPUT_PORT%"=="" set INPUT_PORT=8000
echo Using port: %INPUT_PORT%
echo.

REM Write API_PORT to .env (remove old entry first if exists)
powershell -Command "(Get-Content .env -ErrorAction SilentlyContinue) -notmatch '^API_PORT=' | Set-Content .env"
echo API_PORT=%INPUT_PORT%>> .env
echo.

REM Check if Docker is running
echo Checking Docker status...
docker info >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Docker is not running!
    echo.
    echo Please start Docker Desktop and try again.
    echo.
    pause
    exit /b 1
)

echo Docker is running.
echo.

REM Read version from file
if not exist version.txt (
    echo Creating version.txt with initial version 1.0.0
    echo 1.0.0 > version.txt
)

set /p IMAGE_TAG=<version.txt

REM Remove any trailing whitespace
set IMAGE_TAG=%IMAGE_TAG: =%

set IMAGE_NAME=refresh-es-api
set FULL_IMAGE_NAME=%IMAGE_NAME%:%IMAGE_TAG%

REM Docker Hub settings
set DOCKERHUB_USERNAME=kumarpnq
set DOCKERHUB_REPO=%DOCKERHUB_USERNAME%/refresh-es-api

echo Current version: %IMAGE_TAG%
echo.

REM Ask if user wants to increment version
set /p increment="Increment version? (y/n): "
if "%increment%"=="y" goto :increment_version
goto :skip_increment

:increment_version
echo.
echo Enter new version (current: %IMAGE_TAG%):
set /p NEW_VERSION="New version: "
echo|set /p="%NEW_VERSION%" > version.txt
set IMAGE_TAG=%NEW_VERSION%
set FULL_IMAGE_NAME=%IMAGE_NAME%:%IMAGE_TAG%
set DOCKERHUB_REPO=%DOCKERHUB_USERNAME%/refresh-es-api
echo New version: %IMAGE_TAG%
echo.
goto :continue

:skip_increment
echo Using current version: %IMAGE_TAG%
echo.

:continue
echo ========================================
echo Step 1: Building Docker Image
echo ========================================
echo.

REM Stop and remove existing container
echo Stopping existing container...
docker stop refresh-es-api-prod 2>nul
docker rm refresh-es-api-prod 2>nul

REM Remove existing image
echo Removing existing image...
docker rmi %FULL_IMAGE_NAME% 2>nul

echo Building Docker image: %FULL_IMAGE_NAME%
echo This may take a few minutes...
echo.

REM Build the Docker image
docker build -t %FULL_IMAGE_NAME% -f Dockerfile .

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Docker build failed!
    echo Please check the error messages above.
    pause
    exit /b 1
)

echo.
echo Docker build completed successfully!
echo.

echo ========================================
echo Step 2: Pushing to Docker Hub
echo ========================================
echo.

echo Image: %FULL_IMAGE_NAME%
echo Repository: %DOCKERHUB_REPO%:%IMAGE_TAG%
echo.

REM Docker Hub login
echo Attempting Docker Hub login...
echo.
docker login

if %ERRORLEVEL% NEQ 0 (
    echo Docker login failed!
    pause
    exit /b 1
)

echo Docker login successful!
echo.

REM Tag the image for Docker Hub
echo Tagging image for Docker Hub...
docker tag %FULL_IMAGE_NAME% %DOCKERHUB_REPO%:%IMAGE_TAG%
docker tag %FULL_IMAGE_NAME% %DOCKERHUB_REPO%:latest

echo Images tagged successfully
echo.

REM Push the versioned image
echo Pushing versioned image (%IMAGE_TAG%)...
docker push %DOCKERHUB_REPO%:%IMAGE_TAG%

if %ERRORLEVEL% NEQ 0 (
    echo Push failed!
    pause
    exit /b 1
)

echo Versioned image pushed successfully
echo.

REM Push the latest tag
echo Pushing latest tag...
docker push %DOCKERHUB_REPO%:latest

if %ERRORLEVEL% NEQ 0 (
    echo Push failed!
    pause
    exit /b 1
)

echo Latest image pushed successfully
echo.

echo ========================================
echo Build and Push completed successfully!
echo ========================================
echo.
echo Repository: %DOCKERHUB_REPO%
echo Tags pushed:
echo   - %IMAGE_TAG%
echo   - latest
echo.
echo Your image is now available at:
echo   https://hub.docker.com/r/%DOCKERHUB_REPO%
echo.
pause

