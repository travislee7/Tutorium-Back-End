from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail
from .models import StudentUser, TutorApplication, TutorProfile, BookmarkedTutors, TutorReview, TwoFactorCode, TutorAnalyticsView, RequestFormInfo
from django.conf import settings
import json
import boto3
import logging
import re
from django.db.models import Q, Avg, Sum, Count
from django.core.exceptions import ValidationError
from django.contrib.auth.hashers import make_password, check_password
from botocore.exceptions import BotoCoreError, ClientError
from django.utils.timezone import now, timedelta
from django.core.cache import cache  # Use Django cache for temporary storage
import random
from django.db.models.functions import TruncDate
from django.utils.timezone import now
from datetime import date
 
 
logger = logging.getLogger(__name__)



@csrf_exempt
def send_2fa_code(request):
    try:
        data = json.loads(request.body)
        email = data.get('email')
        mode = data.get('mode', 'signup')  # default to signup

        # If it's a signin flow, skip the signup session check
        if mode == 'signup' and not cache.get(f"signup_{email}"):
            return JsonResponse({'error': 'Signup session not found'}, status=400)


        code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        cache.set(f"2fa_{email}", code, timeout=300)

        send_mail(
            'Your Authentication Code',
            f'Your verification code is: {code}',
            'help.tutorium@gmail.com',
            [email],
        )

        print(f"2FA Code for {email} ({mode}): {code}")

        return JsonResponse({'message': 'Code sent successfully!'}, status=200)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def verify_2fa_code(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')
            code = data.get('code')
            mode = data.get('mode', 'signin')  # default to signin

            print(f"Verifying {email} with code {code} (mode: {mode})")
            stored_code = cache.get(f"2fa_{email}")
            print(f"Stored 2FA code for {email}: {stored_code}")

            if stored_code and stored_code == code:
                if mode == 'signup':
                    user_data = cache.get(f"signup_{email}")
                    if not user_data:
                        return JsonResponse({'error': 'Signup session expired'}, status=400)

                    student = StudentUser.objects.create(
                        first_name=user_data['firstName'],
                        last_name=user_data['lastName'],
                        email=email,
                        password=make_password(user_data['password']),
                        user_type=user_data.get('userType', '')
                    )

                    cache.delete(f"signup_{email}")
                    cache.delete(f"2fa_{email}")
                    return JsonResponse({'message': 'User created successfully!', 'user_id': student.id}, status=201)

                elif mode == 'signin':
                    try:
                        user = StudentUser.objects.get(email=email)
                    except StudentUser.DoesNotExist:
                        return JsonResponse({'error': 'User does not exist'}, status=404)

                    cache.delete(f"2fa_{email}")
                    return JsonResponse({
                        'message': 'Signed in successfully!',
                        'user_id': user.id,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'user_type': user.user_type,
                    }, status=200)

            return JsonResponse({'error': 'Invalid or expired code'}, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def initiate_signup(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')

            if not email:
                return JsonResponse({'error': 'Email is required'}, status=400)

            # Store signup data in cache
            cache.set(f"signup_{email}", data, timeout=600)

            # Generate and cache 2FA code
            code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            cache.set(f"2fa_{email}", code, timeout=300)

            # Send 2FA code by email
            send_mail(
                'Your Verification Code',
                f'Your verification code is: {code}',
                'help.tutorium@gmail.com',
                [email],
            )

            print(f"Code for {email}: {code}")

            return JsonResponse({'message': 'Verification code sent'}, status=200)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def signup(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
 
            # Fallback to default value if userType is not provided
            user_type = data.get('userType', '')  # Default to ''
 
 
            # Hash the password before saving the user
            hashed_password = make_password(data['password'])
 
            # Create a new user
            student = StudentUser.objects.create(
                first_name=data['firstName'],
                last_name=data['lastName'],
                email=data['email'],
                password=hashed_password,  # Consider hashing this in production
                user_type=user_type
            )

            cache.set(f"signup_{data['email']}", {
                'firstName': data['firstName'],
                'lastName': data['lastName'],
                'password': data['password'],
                'userType': user_type
            }, timeout=600)
 
            return JsonResponse({'message': 'User created successfully!', 'user_id': student.id}, status=201)
 
        except Exception as e:
            return JsonResponse({'message': 'Failed to create user', 'error': str(e)}, status=400)
 
    return JsonResponse({'message': 'Invalid request method.'}, status=400)
 
@csrf_exempt
def get_student_user_data(request):
    if request.method == 'GET':
        user_id = request.GET.get('user_id')
        if not user_id:
            return JsonResponse({'error': 'User ID is required'}, status=400)
 
        try:
            student_user = StudentUser.objects.get(id=user_id)
            return JsonResponse({
                'first_name': student_user.first_name,
                'last_name': student_user.last_name,
                'email': student_user.email,
            }, status=200)
        except StudentUser.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
 
    return JsonResponse({'error': 'Invalid request method'}, status=405)
 
 
@csrf_exempt
def application(request):
    if request.method == 'POST':
        try:
            # Parse the JSON data from the request body
            data = json.loads(request.body)
            email = data.get('email', '')
            question_one = data.get('questionOne', '')
            question_two = data.get('questionTwo', '')
 
            # Validate required fields
            if not email or not question_one or not question_two:
                return JsonResponse({'error': 'All fields are required.'}, status=400)
            
 
                        # Get the StudentUser instance
            try:
                student = StudentUser.objects.get(email=email)
            except StudentUser.DoesNotExist:
                return JsonResponse({'error': 'User not found.'}, status=404)
 
            # Create or update the application with just the foreign key and status
            TutorApplication.objects.update_or_create(
                user=student,
                defaults={
                    'approve_status': 'pending'
                }
            )
 
            # Email 1: Send to your own email with the form data
            subject_to_self = "New Tutor Application Received"
            message_to_self = (
                f"Tutor Application Submitted:\n\n"
                f"Email: {email}\n\n"
                f"Why do you think you can be a tutor? List your school and experience:\n"
                f"{question_one}\n\n"
                f"List Your Qualifications. Have you ever worked with a different tutoring app?:\n"
                f"{question_two}\n\n"
            )
 
            sender_email = "help.tutorium@gmail.com"  # Your Gmail address
            your_email = "help.tutorium@gmail.com"  # Your email to receive the form data
 
            send_mail(subject_to_self, message_to_self, sender_email, [your_email])
 
            # Email 2: Send to the applicant (recipient email) with a confirmation
            subject_to_recipient = "Your Tutor Application Submission"
            message_to_recipient = (
                f"Dear Applicant,\n\n"
                f"Thank you for submitting your application to become a tutor. Here is a summary of your submission:\n\n"
                f"Why do you think you can be a tutor? List your school and experience:\n"
                f"{question_one}\n\n"
                f"List Your Qualifications. Have you ever worked with a different tutoring app?:\n"
                f"{question_two}\n\n"
                f"We will review your application and get back to you shortly.\n\n"
                f"Best regards,\nThe Tutorium Team"
            )
 
            recipient_email = email  # Use the submitted email as the recipient
 
            send_mail(subject_to_recipient, message_to_recipient, sender_email, [recipient_email])
 
            # Return success response
            return JsonResponse({'message': 'Application received successfully!'}, status=200)
 
        except Exception as e:
            # Handle any errors
            return JsonResponse({'error': str(e)}, status=500)
 
    # If not a POST request, return a 405 Method Not Allowed
    return JsonResponse({'error': 'Invalid request method.'}, status=405)
 
@csrf_exempt
def tutor_approve_status(request):
    if request.method == 'GET':
        user_id = request.GET.get('user_id')
        if not user_id:
            return JsonResponse({'error': 'User ID is required'}, status=400)
 
        try:
            tutor_application = TutorApplication.objects.get(user_id=user_id)
            return JsonResponse({'approve_status': tutor_application.approve_status}, status=200)
        except TutorApplication.DoesNotExist:
            return JsonResponse({'approve_status': None}, status=404)
 
    return JsonResponse({'error': 'Invalid request method'}, status=405)
 
 
@csrf_exempt  # Remove csrf_exempt in production and secure the endpoint
def tutor_profile_status(request):
    if request.method == 'GET':
        try:
            # Retrieve the user_id from query parameters
            user_id = request.GET.get('user_id')
 
            if not user_id:
                return JsonResponse({'error': 'User ID is required'}, status=400)
 
            # Check if a TutorProfile exists for the given user_id
            try:
                tutor_profile = TutorProfile.objects.get(user_id=user_id)
                return JsonResponse({'profile_complete': tutor_profile.profile_complete}, status=200)
            except TutorProfile.DoesNotExist:
                return JsonResponse({'profile_complete': None}, status=404)
 
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
 
    return JsonResponse({'error': 'Invalid request method'}, status=405)
 
 
# Allowed image file extensions
ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png']
ALLOWED_CONTENT_TYPES = ['image/jpeg', 'image/png']
 
 
def validate_image_file(profile_picture):
    """Validate image file extension and content type."""
    if not profile_picture.name.split('.')[-1].lower() in ALLOWED_EXTENSIONS:
        raise ValidationError("Only JPG, JPEG, and PNG file types are allowed.")
    if profile_picture.content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError("Invalid image content type. Only JPG, JPEG, and PNG are allowed.")
 
 
@csrf_exempt
def save_tutor_profile(request):
    if request.method == 'POST':
        try:
            user_id = request.POST.get('user_id')
            bio = request.POST.get('bio')
            subjects = request.POST.get('subjects')  # Comma-separated string
            location = request.POST.get('location')
            language = request.POST.get('language')  # Comma-separated string
            profile_picture = request.FILES.get('profilePic')
            existing_profile_picture = request.POST.get('existingProfilePic')
            gender = request.POST.get('gender')
            hourly_rate = request.POST.get('hourly_rate')

 
            if not user_id:
                return JsonResponse({'error': 'User ID is required'}, status=400)
 
            try:
                user = StudentUser.objects.get(id=user_id)
            except StudentUser.DoesNotExist:
                return JsonResponse({'error': 'User not found'}, status=404)
 
            # Validate and upload profile picture if provided
            profile_pic_url = None
            if profile_picture:
                try:
                    validate_image_file(profile_picture)
                except ValidationError as e:
                    return JsonResponse({'error': str(e)}, status=400)
 
                # Upload new profile picture to S3
                s3 = boto3.client(
                    's3',
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
                )
                bucket_name = 'tutor-profile-pics'
                file_extension = profile_picture.name.split('.')[-1].lower()
                file_name = f"tutor-profile-pics/{user_id}_profile.{file_extension}"
                
                s3.upload_fileobj(
                    profile_picture,
                    bucket_name,
                    file_name,
                    ExtraArgs={
                        'ContentType': profile_picture.content_type,
                        # 'ACL': 'public-read'
                    }
                )
                profile_pic_url = f"https://{bucket_name}.s3.amazonaws.com/{file_name}"
            
            elif existing_profile_picture:
                # Use the existing profile picture URL
                profile_pic_url = existing_profile_picture
 
            # Update or create the tutor profile
            profile, created = TutorProfile.objects.update_or_create(
                user=user,
                defaults={
                    'bio': bio,
                    'subjects': subjects,
                    'location': location,
                    'language': language,
                    'profile_picture': profile_pic_url,
                    'profile_complete': 'yes',
                    'gender': gender,
                    'hourly_rate': hourly_rate,
                }
            )
 
            return JsonResponse({'message': 'Profile saved successfully!'}, status=200)
 
        except ValidationError as ve:
            return JsonResponse({'error': str(ve)}, status=400)
        except Exception as e:
            print(e)  # Log the error for debugging
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)
 
    return JsonResponse({'error': 'Invalid request method'}, status=405)
 
 
@csrf_exempt
def get_tutor_profile(request):
    if request.method == 'GET':
        try:
            user_id = request.GET.get('user_id')
 
            if not user_id:
                return JsonResponse({'error': 'User ID is required'}, status=400)
 
            try:
                tutor_profile = TutorProfile.objects.get(user_id=user_id)
                return JsonResponse({
                    'bio': tutor_profile.bio,
                    'profile_picture': tutor_profile.profile_picture,
                    'subjects': tutor_profile.subjects,
                    'location': tutor_profile.location,
                    'language': tutor_profile.language,
                    'profile_complete': tutor_profile.profile_complete,
                    'gender': tutor_profile.gender,
                    'hourly_rate': str(tutor_profile.hourly_rate) if tutor_profile.hourly_rate is not None else '',
                    'verified': tutor_profile.verified or '',  # <-- added this line

                }, status=200)
            except TutorProfile.DoesNotExist:
                return JsonResponse({'error': 'Profile not found'}, status=404)
 
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
 
    return JsonResponse({'error': 'Invalid request method'}, status=405)
 
 
# @csrf_exempt
# def search_tutors(request):
#     if request.method == 'GET':
#         subject = request.GET.get('subject', '').strip()
#         location = request.GET.get('location', '').strip()
#         language = request.GET.get('language', '').strip()
 
#         # Start with all tutors and apply filters for all aspects of the query
#         filters = Q()
#         if subject:
#             filters &= Q(subjects__icontains=subject)  # Subject must contain the query
#         if location:
#             filters &= Q(location__icontains=location)  # Location must contain the query
#         if language:
#             filters &= Q(language__icontains=language)  # Language must contain the query
 
#         # Only return tutors where all filters match and profile is complete
#         tutors = TutorProfile.objects.filter(filters, profile_complete='yes').values(
#             'user__id',
#             'user__first_name',
#             'user__last_name',
#             'profile_picture',
#             'subjects',
#             'location',
#             'language',
#             'bio',
#             'average_rating',
#             'gender',
#             'hourly_rate',  # <--- ADD THIS
#         )
 
#         return JsonResponse(list(tutors), safe=False)
 
#     return JsonResponse({'error': 'Invalid request method'}, status=400)


@csrf_exempt
def search_tutors(request):
    if request.method == 'GET':
        subjects = request.GET.getlist('subjects')  # AND logic
        locations = request.GET.getlist('locations')  # OR logic
        languages = request.GET.getlist('languages')  # OR logic
        gender = request.GET.get('gender', '').strip()
        max_rate = request.GET.get('max_rate', '').strip()

        filters = Q()

        # Subjects: AND logic
        for subject in subjects:
            filters &= Q(subjects__icontains=subject)

        # Locations: OR logic
        if locations:
            location_q = Q()
            for loc in locations:
                location_q |= Q(location__icontains=loc)
            filters &= location_q

        # Languages: OR logic
        if languages:
            language_q = Q()
            for lang in languages:
                language_q |= Q(language__icontains=lang)
            filters &= language_q

        # Gender filter
        if gender:
            filters &= Q(gender__iexact=gender)

        # Hourly Rate filter
        if max_rate:
            try:
                filters &= Q(hourly_rate__lte=float(max_rate))
            except ValueError:
                pass

        tutors = TutorProfile.objects.filter(filters, profile_complete='yes').values(
            'user__id',
            'user__first_name',
            'user__last_name',
            'profile_picture',
            'subjects',
            'location',
            'language',
            'bio',
            'average_rating',
            'gender',
            'hourly_rate',
            'verified',  # ← Add this line
        )

        return JsonResponse(list(tutors), safe=False)

    return JsonResponse({'error': 'Invalid request method'}, status=400)
 
@csrf_exempt
def tutor_details(request, tutor_id):
    if request.method == 'GET':
        try:
            tutor = TutorProfile.objects.filter(user__id=tutor_id, profile_complete='yes').values(
                'user__id',  # Include the user ID
                'user__first_name',
                'user__last_name',
                'profile_picture',
                'bio',
                'subjects',
                'location',
                'language',
                'average_rating',  # Include average_rating in the response
                'gender',          # <-- Add this
                'hourly_rate'      # <-- And this
                'verified',  # Include verified field

            ).first()
 
            if not tutor:
                logger.error(f"Tutor with ID {tutor_id} not found or profile incomplete.")
                return JsonResponse({'error': 'Tutor not found'}, status=404)
 
            return JsonResponse(tutor, safe=False)
        except Exception as e:
            logger.error(f"Error fetching tutor details for tutor_id {tutor_id}: {str(e)}")  # Log the error
            return JsonResponse({'error': f"Internal Server Error: {str(e)}"}, status=500)
 
    return JsonResponse({'error': 'Invalid request method'}, status=400)
 
 
@csrf_exempt
def signin(request):
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        email = data.get('email')
        password = data.get('password')
 
        try:
            user = StudentUser.objects.get(email=email)
            if check_password(password, user.password):  # Assumes passwords are hashed
                return JsonResponse({
                    'status': 'success',
                    'user_id': user.id,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'user_type': user.user_type,
                })
            else:
                return JsonResponse({'status': 'fail', 'message': 'Invalid password'}, status=400)
        except StudentUser.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message': 'User does not exist'}, status=404)
    else:
        return JsonResponse({'status': 'fail', 'message': 'Invalid request method'}, status=405)
 
@csrf_exempt
def send_tutor_request_email(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            first_name = data.get('firstName')
            last_name = data.get('lastName')
            email = data.get('email')
            description = data.get('description')
            tutor_first_name = data.get('tutorFirstName')
            tutor_last_name = data.get('tutorLastName')
            tutor_id = data.get('tutorId')
 
            if not all([first_name, last_name, email, description, tutor_first_name, tutor_last_name, tutor_id]):
                return JsonResponse({'error': 'All fields are required.'}, status=400)
 
            # Find tutor's email using the tutor_id
            try:
                tutor = StudentUser.objects.get(pk=tutor_id)
                tutor_email = tutor.email
            except StudentUser.DoesNotExist:
                return JsonResponse({'error': 'Tutor not found.'}, status=404)
 
            # Email content
            admin_email_subject = 'New Tutor Request'
            admin_email_body = f"""
                New tutor request for {tutor_first_name} {tutor_last_name} (Tutor Email: {tutor_email}):
                - Student Name: {first_name} {last_name}
                - Student Email: {email}
                - Description: {description}
            """
            user_email_subject = 'Request Received'
            user_email_body = f"""
                Hi {first_name},
 
                Thank you for reaching out to us. We have received your request for tutor {tutor_first_name} {tutor_last_name}. We will get back to you shortly.
 
                Best regards,
                The Tutorium Team
            """
 
            # Send email to the admin
            send_mail(
                subject=admin_email_subject,
                message=admin_email_body,
                from_email='help.tutorium@gmail.com',
                recipient_list=['help.tutorium@gmail.com'],
            )
 
            # Send confirmation email to the user
            send_mail(
                subject=user_email_subject,
                message=user_email_body,
                from_email='help.tutorium@gmail.com',
                recipient_list=[email],
            )
 
            return JsonResponse({'message': 'Emails sent successfully.'})
 
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
 
    return JsonResponse({'error': 'Invalid request method.'}, status=405)
 
 
@csrf_exempt
def bookmark_tutor(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            student_id = data.get('studentID')
            tutor_id = data.get('tutorID')
 
            if not student_id or not tutor_id:
                return JsonResponse({'error': 'studentID and tutorID are required'}, status=400)
 
            # Check if this bookmark already exists
            existing = BookmarkedTutors.objects.filter(student_id=student_id, tutor_id=tutor_id).first()
            if existing:
                return JsonResponse({'message': 'Tutor already bookmarked'}, status=200)
 
            # Save the bookmark
            bookmark = BookmarkedTutors(student_id=student_id, tutor_id=tutor_id)
            bookmark.save()
            return JsonResponse({'message': 'Tutor bookmarked successfully'}, status=201)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)
    
@csrf_exempt
def is_tutor_bookmarked(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            student_id = data.get('studentID')
            tutor_id = data.get('tutorID')
 
            if not student_id or not tutor_id:
                return JsonResponse({'error': 'studentID and tutorID are required'}, status=400)
 
            # Check if the bookmark exists
            exists = BookmarkedTutors.objects.filter(student_id=student_id, tutor_id=tutor_id).exists()
            return JsonResponse({'isBookmarked': exists}, status=200)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)
    
@csrf_exempt
def unbookmark_tutor(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            student_id = data.get('studentID')
            tutor_id = data.get('tutorID')
 
            if not student_id or not tutor_id:
                return JsonResponse({'error': 'studentID and tutorID are required'}, status=400)
 
            # Find and delete the bookmark
            bookmark = BookmarkedTutors.objects.filter(student_id=student_id, tutor_id=tutor_id).first()
            if bookmark:
                bookmark.delete()
                return JsonResponse({'message': 'Tutor unbookmarked successfully'}, status=200)
            else:
                return JsonResponse({'error': 'Bookmark does not exist'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)
    
 
@csrf_exempt
def get_bookmarked_tutors(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            student_id = data.get('studentID')
 
            if not student_id:
                return JsonResponse({'error': 'studentID is required'}, status=400)
 
            # Get all bookmarked tutors for the student
            bookmarks = BookmarkedTutors.objects.filter(student_id=student_id)
            if not bookmarks.exists():
                return JsonResponse({'bookmarked_tutors': []}, status=200)
 
            # Retrieve tutor details for each bookmarked tutor
            bookmarked_tutors = []
            for bookmark in bookmarks:
                tutor_profile = TutorProfile.objects.filter(user_id=bookmark.tutor_id).first()
                if tutor_profile:
                    bookmarked_tutors.append({
                        'tutorID': bookmark.tutor_id,
                        'name': f"{tutor_profile.user.first_name} {tutor_profile.user.last_name}",
                        'subjects': tutor_profile.subjects,
                        'location': tutor_profile.location,
                        'languages': tutor_profile.language,
                        'profile_picture': tutor_profile.profile_picture,  # Include S3 URL
                        'verified': tutor_profile.verified,  # ✅ Include verified subjects

                    })
 
            return JsonResponse({'bookmarked_tutors': bookmarked_tutors}, status=200)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)
 
# @csrf_exempt
# def add_review(request, tutor_id):
#     if request.method == 'POST':
#         try:
#             data = json.loads(request.body)
#             student_id = data.get('studentID')
#             rating = data.get('rating')
#             comment = data.get('comment')
 
#             # Fetch student and tutor instances
#             student = StudentUser.objects.get(id=student_id)
#             tutor = TutorProfile.objects.get(user_id=tutor_id)
 
#             # Create and save the review
#             review = TutorReview.objects.create(
#                 student=student,
#                 tutor=tutor,
#                 rating=rating,
#                 comment=comment
#             )
#             review.save()
 
#             return JsonResponse({'message': 'Review submitted successfully!'}, status=201)
#         except StudentUser.DoesNotExist:
#             return JsonResponse({'error': 'Invalid studentID'}, status=404)
#         except TutorProfile.DoesNotExist:
#             return JsonResponse({'error': 'Invalid tutorID'}, status=404)
#         except Exception as e:
#             return JsonResponse({'error': str(e)}, status=500)
#     else:
#         return JsonResponse({'error': 'Invalid request method'}, status=405)
 
# @csrf_exempt
# def add_review(request, tutor_id):
#     if request.method == 'POST':
#         try:
#             data = json.loads(request.body)
#             student_id = data.get('studentID')
#             rating = data.get('rating')
#             comment = data.get('comment')
 
#             # Validate input
#             if not all([student_id, tutor_id, rating, comment]):
#                 return JsonResponse({'error': 'You must provide all the required fields.'}, status=400)
 
#             # Validate rating
#             if not (1 <= rating <= 5):
#                 return JsonResponse({'error': 'Rating must be between 1 and 5.'}, status=400)
 
#             # Check if the student already submitted a review for this tutor
#             if TutorReview.objects.filter(student_id=student_id, tutor_id=tutor_id).exists():
#                 return JsonResponse({
#                     'error': 'You have already submitted a review for this tutor.'
#                 }, status=400)
 
        #     # Fetch student and tutor instances
        #     student = StudentUser.objects.get(id=student_id)
        #     tutor = TutorProfile.objects.get(user_id=tutor_id)
 
        #     # Create and save the review
        #     review = TutorReview.objects.create(
        #         student=student,
        #         tutor=tutor,
        #         rating=rating,
        #         comment=comment
        #     )
        #     review.save()
 
        #     # Update tutor's average rating
        #     reviews = TutorReview.objects.filter(tutor=tutor)
        #     avg_rating = reviews.aggregate(Avg('rating'))['rating__avg']
        #     tutor.average_rating = round(avg_rating, 2)  # rounding to 2 decimal places
        #     tutor.save()
 
        #     return JsonResponse({'message': 'Review submitted successfully!'}, status=201)
        # except StudentUser.DoesNotExist:
        #     return JsonResponse({'error': 'Invalid studentID'}, status=404)
        # except TutorProfile.DoesNotExist:
        #     return JsonResponse({'error': 'Invalid tutorID'}, status=404)
        # except Exception as e:
        #     return JsonResponse({'error': str(e)}, status=500)
    # else:
    #     return JsonResponse({'error': 'Invalid request method'}, status=405)




# @csrf_exempt
# def add_review(request, tutor_id):
#     if request.method == 'POST':
#         try:
#             data = json.loads(request.body)
#             student_id = data.get('studentID')
#             rating = data.get('rating')
#             comment = data.get('comment')

#             # Directly create and save the review without any checks
#             review = TutorReview.objects.create(
#                 student_id=student_id,
#                 tutor_id=tutor_id,
#                 rating=rating,
#                 comment=comment
#             )

#             # Save the review
#             review.save()

#             return JsonResponse({'message': 'Review submitted successfully!'}, status=201)

#         except Exception as e:
#             return JsonResponse({'error': str(e)}, status=500)
#     else:
#         return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def add_review(request, tutor_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            student_id = data.get('studentID')
            rating = data.get('rating')
            comment = data.get('comment')

            # Log the received data
            print(f"Received: student_id={student_id}, tutor_id={tutor_id}, rating={rating}, comment={comment}")

            # Create and save the review directly with IDs
            review = TutorReview.objects.create(
                student_id=student_id,
                tutor_id=tutor_id,
                rating=rating,
                comment=comment
            )

            # Save the review
            review.save()
            print(f"Review created successfully: {review}")

            # Calculate the new average rating
            reviews = TutorReview.objects.filter(tutor_id=tutor_id)
            total_ratings = sum([rev.rating for rev in reviews])
            num_reviews = reviews.count()

            # Safeguard division by zero
            if num_reviews == 0:
                average_rating = 0.0
            else:
                average_rating = round(total_ratings / num_reviews, 2)

            # Update the average rating in TutorProfile
            TutorProfile.objects.filter(user_id=tutor_id).update(average_rating=average_rating)


            return JsonResponse({'message': 'Review submitted successfully!'}, status=201)

        except Exception as e:
            print(f"Error while creating review: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


    

# @csrf_exempt
# def log_tutor_view(request):
#     if request.method == 'POST':
#         try:
#             data = json.loads(request.body)
#             tutor_id = data.get('tutor_id')
#             viewer_id = data.get('viewer_id')  # May be None

#             tutor = TutorProfile.objects.get(user__id=tutor_id)
#             viewer = None

#             if viewer_id:
#                 try:
#                     viewer = StudentUser.objects.get(id=viewer_id)
#                 except StudentUser.DoesNotExist:
#                     viewer = None  # Gracefully handle invalid viewer ID

#             # Try to find existing record (same tutor and viewer — or viewer is null)
#             analytics, created = TutorAnalyticsView.objects.get_or_create(
#                 tutor=tutor,
#                 viewer=viewer,
#                 defaults={'view_count': 1}
#             )

#             if not created:
#                 analytics.view_count += 1

#             analytics.save()

#             return JsonResponse({
#                 'message': 'View logged successfully',
#                 'view_count': analytics.view_count,
#                 'created': created,
#             })

#         except Exception as e:
#             return JsonResponse({'error': str(e)}, status=400)

#     return JsonResponse({'error': 'Invalid method'}, status=405)

@csrf_exempt
def log_tutor_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            tutor_id = data.get('tutor_id')
            viewer_id = data.get('viewer_id')  # May be None

            tutor = TutorProfile.objects.get(user__id=tutor_id)
            viewer = None

            if viewer_id:
                try:
                    viewer = StudentUser.objects.get(id=viewer_id)
                except StudentUser.DoesNotExist:
                    viewer = None

            today = date.today()

            # # Find today's record for this tutor and viewer (or None)
            # analytics = TutorAnalyticsView.objects.annotate(
            #     view_date=TruncDate('timestamp')
            # ).filter(
            #     tutor=tutor,
            #     viewer=viewer,
            #     view_date=today
            # ).first()

            TutorAnalyticsView.objects.create(
                tutor=tutor,
                viewer=viewer,
                view_count=1  # optional if default is 1
            )

            if analytics:
                analytics.view_count += 1
                analytics.save()
                created = False
            else:
                analytics = TutorAnalyticsView.objects.create(
                    tutor=tutor,
                    viewer=viewer,
                    view_count=1,
                    timestamp=now()  # Optional; auto_now_add covers this
                )
                created = True

            return JsonResponse({
                'message': 'View logged successfully',
                'view_count': analytics.view_count,
                'created': created,
            })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'error': 'Invalid method'}, status=405)


def get_view_count(request, user_id):
    try:
        tutor_profile = TutorProfile.objects.get(user__id=user_id)

        # Sum the view_count for all analytics records for this tutor
        total_views = TutorAnalyticsView.objects.filter(tutor=tutor_profile).aggregate(
            total=Sum('view_count')
        )['total'] or 0

        return JsonResponse({'view_count': total_views})
    
    except TutorProfile.DoesNotExist:
        return JsonResponse({'error': 'Tutor profile not found'}, status=404)
    

#@api_view(['GET'])
def get_views_per_day(request, user_id):
    try:
        tutor_profile = TutorProfile.objects.get(user__id=user_id)

        # Group by date, sum view_count
        views_by_day = (
            TutorAnalyticsView.objects
            .filter(tutor=tutor_profile)
            .annotate(date=TruncDate('timestamp'))
            .values('date')
            .annotate(total_views=Sum('view_count'))
            .order_by('date')
        )

        # Convert QuerySet to a simple list of {date, total_views}
        data = [
            {'date': entry['date'].strftime('%Y-%m-%d'), 'views': entry['total_views']}
            for entry in views_by_day
        ]

        return JsonResponse({'history': data})

    except TutorProfile.DoesNotExist:
        return JsonResponse({'error': 'Tutor profile not found'}, status=404)


def get_viewers(request, user_id):
    try:
        tutor_profile = TutorProfile.objects.get(user__id=user_id)

        views = (
            TutorAnalyticsView.objects
            .filter(tutor=tutor_profile)
            .order_by('-timestamp')
            .select_related('viewer')
        )

        data = []
        for view in views:
            if view.viewer:
                name = {
                    'first_name': view.viewer.first_name,
                    'last_name': view.viewer.last_name,
                }
            else:
                name = {
                    'first_name': 'Anonymous',
                    'last_name': '',
                }

            data.append({
                **name,
                'timestamp': view.timestamp,
            })

        return JsonResponse({'viewers': data})

    except TutorProfile.DoesNotExist:
        return JsonResponse({'error': 'Tutor profile not found'}, status=404)
    

@csrf_exempt
def save_request_form_info(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            requester_first_name = data.get('requesterFirstName')
            requester_last_name = data.get('requesterLastName')
            requester_email = data.get('requesterEmail')
            requester_description = data.get('requesterDescription')
            tutor_id = data.get('tutorID')

            # Save to database
            RequestFormInfo.objects.create(
                requesterFirstName=requester_first_name,
                requesterLastName=requester_last_name,
                requesterEmail=requester_email,
                requesterDescription=requester_description,
                tutorID=tutor_id
            )

            return JsonResponse({'message': 'Request form info saved successfully!'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@csrf_exempt
def get_tutor_request_count(request, tutor_id):
    if request.method == 'GET':
        try:
            request_count = RequestFormInfo.objects.filter(tutorID=tutor_id).count()
            return JsonResponse({'request_count': request_count})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def get_tutor_requests(request, tutor_id):
    if request.method == 'GET':
        try:
            requests = RequestFormInfo.objects.filter(tutorID=tutor_id).order_by('-created_at')
            request_list = [
                {
                    'requesterDescription': r.requesterDescription,
                    'created_at': r.created_at.isoformat(),
                }
                for r in requests
            ]
            return JsonResponse({'requests': request_list})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

# @csrf_exempt
# def list_reviews(request, tutor_id):
#     try:
#         # Filter reviews based on the integer tutor_id
#         qs = TutorReview.objects.filter(tutor_id=tutor_id).order_by('-created_at')

#         # Prepare the list of reviews
#         reviews = []
#         for r in qs:
#             reviews.append({
#                 "id": r.pk,
#                 "student_id": r.student_id,
#                 "tutor_id": r.tutor_id,
#                 "rating": r.rating,
#                 "comment": r.comment,
#                 "created_at": r.created_at.isoformat(),
#             })

#         # Log the response for debugging
#         print(f"Returning {len(reviews)} reviews for tutor_id: {tutor_id}")

#         return JsonResponse({"reviews": reviews}, safe=False)

#     except Exception as e:
#         print(f"Error fetching reviews: {str(e)}")
#         return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def list_reviews(request, tutor_id):
    try:
        # Get all matching reviews for the given tutor_id
        qs = TutorReview.objects.filter(tutor_id=tutor_id).order_by('-created_at')

        reviews = []
        for r in qs:
            # Fetch student details using the student_id from the review
            try:
                student = StudentUser.objects.get(pk=r.student_id)
                student_name = f"{student.first_name} {student.last_name}"
            except StudentUser.DoesNotExist:
                student_name = "Unknown Student"

            # Append the review details including the student name
            reviews.append({
                "id": r.pk,
                "student_id": r.student_id,
                "tutor_id": r.tutor_id,
                "student_name": student_name,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at.isoformat(),
            })

        # Log the number of reviews returned
        print(f"Returning {len(reviews)} reviews for tutor_id: {tutor_id}")

        return JsonResponse({"reviews": reviews}, safe=False)

    except Exception as e:
        print(f"Error fetching reviews: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def verify_subject(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        user_id = data.get('user_id')
        subject = data.get('subject')

        try:
            profile = TutorProfile.objects.get(user__id=user_id)
            current = profile.verified.split(',') if profile.verified else []
            if subject not in current:
                current.append(subject)
                profile.verified = ','.join(current)
                profile.save()

            return JsonResponse({'status': 'success'})
        except TutorProfile.DoesNotExist:
            return JsonResponse({'error': 'Tutor profile not found'}, status=404)

    return JsonResponse({'error': 'Invalid request method'}, status=405)
